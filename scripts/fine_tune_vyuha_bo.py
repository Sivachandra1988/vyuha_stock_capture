"""
=============================================================================
🏛️ VYUHA-BO INSTITUTIONAL STRATEGY OPTIMIZER & FINE-TUNING ENGINE
=============================================================================
High-Speed Vectorized Simulation across 54 Liquid NSE F&O Stocks (2 Years Daily OHLCV)
Dual Performance Analytics:
1. Spot / Equity Performance (Win Rate %, Profit Factor, Realized RR, Max DD %)
2. Options Contract Performance (ATM Delta 0.50, Theta Decay, Lot Size, Net ₹ PnL)
=============================================================================
"""

import os
import sys
import glob
import json
import time
import itertools
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
DATA_DIR = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "backtest_cache")

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from scanner import vyuha, options

# ---------------------------------------------------------------------------
# 1. PRECOMPUTE BAR FEATURES FOR 54 STOCKS (MEMORY PIPELINE)
# ---------------------------------------------------------------------------

def precompute_stock_bars(parquet_path):
    ticker = os.path.basename(parquet_path).replace(".parquet", "")
    try:
        df = pd.read_parquet(parquet_path)
    except Exception:
        return ticker, []

    df = vyuha.clean_dataframe(df)
    if len(df) < 120:
        return ticker, []

    df['ATR_14'] = vyuha.calculate_atr(df, 14)
    df['EMA_9'] = vyuha.calculate_ema(df['Close'], 9)
    df['EMA_21'] = vyuha.calculate_ema(df['Close'], 21)
    df['SMA_50'] = df['Close'].rolling(50).mean()
    df['SMA_200'] = df['Close'].rolling(200).mean()
    df['Vol_SMA_20'] = df['Volume'].rolling(20).mean()

    lot_size = options.get_stock_lot_size(ticker, float(df['Close'].iloc[-1]))

    bars = []
    n = len(df)
    highs = df['High'].values
    lows = df['Low'].values
    opens = df['Open'].values
    closes = df['Close'].values
    volumes = df['Volume'].values
    atrs = df['ATR_14'].values
    ema9s = df['EMA_9'].values
    ema21s = df['EMA_21'].values
    sma50s = df['SMA_50'].values
    sma200s = df['SMA_200'].values
    vol_smas = df['Vol_SMA_20'].values
    dates = df.index.strftime("%Y-%m-%d").values

    for i in range(100, n):
        c_open = opens[i]
        c_high = highs[i]
        c_low = lows[i]
        c_close = closes[i]
        c_vol = volumes[i]
        atr = atrs[i]
        if np.isnan(atr) or atr <= 0:
            continue

        c_range = max(c_high - c_low, 0.001)
        lower_wick_ratio = (min(c_open, c_close) - c_low) / c_range
        upper_wick_ratio = (c_high - max(c_open, c_close)) / c_range

        lookback_slice_h = highs[max(0, i-60):i]
        lookback_slice_l = lows[max(0, i-60):i]
        rolling_high = np.max(lookback_slice_h)
        rolling_low = np.min(lookback_slice_l)
        swing_range = rolling_high - rolling_low
        if swing_range <= 0:
            continue

        demand_top = rolling_low + (0.50 * swing_range)
        demand_bot = rolling_low + (0.20 * swing_range)
        supply_top = rolling_high - (0.20 * swing_range)
        supply_bot = rolling_high - (0.50 * swing_range)

        sma200 = sma200s[i]
        sma50 = sma50s[i]
        vol_sma = vol_smas[i]

        bars.append({
            "idx": i,
            "date": dates[i],
            "open": c_open,
            "high": c_high,
            "low": c_low,
            "close": c_close,
            "atr": atr,
            "lower_wick": lower_wick_ratio,
            "upper_wick": upper_wick_ratio,
            "demand_top": demand_top,
            "demand_bot": demand_bot,
            "supply_top": supply_top,
            "supply_bot": supply_bot,
            "sma200": sma200,
            "sma50": sma50,
            "ema9": ema9s[i],
            "ema21": ema21s[i],
            "vol": c_vol,
            "vol_sma": vol_sma,
            "lot_size": lot_size
        })

    return ticker, bars


def load_precomputed_dataset():
    parquet_files = glob.glob(os.path.join(CACHE_DIR, "*.parquet"))
    print(f"Loading and precomputing features for {len(parquet_files)} cached stocks...")
    dataset = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = ex.map(precompute_stock_bars, parquet_files)
        for ticker, bars in results:
            if bars and len(bars) > 50:
                dataset[ticker] = bars
    print(f"Precomputed {len(dataset)} stocks in pure memory.")
    return dataset


# ---------------------------------------------------------------------------
# 2. VECTORIZED TRADE SIMULATOR (SPOT + OPTIONS DUAL ANALYTICS)
# ---------------------------------------------------------------------------

def simulate_strategy(dataset, params):
    rr_target = params.get("rr_target", 2.2)
    be_trigger_rr = params.get("be_trigger_rr", 1.0)
    min_atr_risk = params.get("min_atr_risk", 1.5)
    max_atr_risk = params.get("max_atr_risk", 2.5)
    wick_thresh = params.get("wick_thresh", 0.28)
    trap_thresh = params.get("trap_thresh", 0.25)
    vol_mult = params.get("vol_mult", 0.85)
    max_holding = params.get("max_holding", 15)
    use_bo_filter = params.get("use_bo_filter", True)
    mode = params.get("mode", "BOTH") # BOTH, LONG_ONLY, SHORT_ONLY

    all_trades = []

    for ticker, bars in dataset.items():
        n = len(bars)
        active_trade = None

        for i in range(n):
            bar = bars[i]
            c_high = bar['high']
            c_low = bar['low']
            c_close = bar['close']
            c_open = bar['open']
            c_date = bar['date']
            atr = bar['atr']
            lot_size = bar['lot_size']

            # -----------------------------------------------------------------
            # 1. TRADE MANAGEMENT (If in trade)
            # -----------------------------------------------------------------
            if active_trade is not None:
                active_trade['holding'] += 1
                entry = active_trade['entry']
                sl = active_trade['sl']
                t1 = active_trade['t1']
                risk = active_trade['risk']
                direction = active_trade['direction']
                is_be = active_trade['is_be']

                trade_closed = False
                exit_price = None
                exit_reason = None

                if direction == "LONG":
                    # Break-Even Trail
                    if be_trigger_rr < 900.0 and c_high >= (entry + be_trigger_rr * risk) and not is_be:
                        active_trade['sl'] = entry + (0.05 * risk)
                        active_trade['is_be'] = True
                        sl = active_trade['sl']

                    # Check Target
                    if c_high >= t1:
                        trade_closed = True
                        exit_price = t1
                        exit_reason = "TARGET"
                    elif c_low <= sl:
                        trade_closed = True
                        exit_price = sl
                        exit_reason = "BE" if is_be else "SL"
                    elif active_trade['holding'] >= max_holding:
                        trade_closed = True
                        exit_price = c_close
                        exit_reason = "TIME_EXPIRY"

                elif direction == "SHORT":
                    # Break-Even Trail
                    if be_trigger_rr < 900.0 and c_low <= (entry - be_trigger_rr * risk) and not is_be:
                        active_trade['sl'] = entry - (0.05 * risk)
                        active_trade['is_be'] = True
                        sl = active_trade['sl']

                    # Check Target
                    if c_low <= t1:
                        trade_closed = True
                        exit_price = t1
                        exit_reason = "TARGET"
                    elif c_high >= sl:
                        trade_closed = True
                        exit_price = sl
                        exit_reason = "BE" if is_be else "SL"
                    elif active_trade['holding'] >= max_holding:
                        trade_closed = True
                        exit_price = c_close
                        exit_reason = "TIME_EXPIRY"

                if trade_closed:
                    holding_days = active_trade['holding']
                    spot_pnl_pts = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
                    spot_pnl_pct = (spot_pnl_pts / entry) * 100.0

                    # Options Contract Simulation:
                    # Delta ~ 0.50 ATM, Theta decay ~ 0.04 * ATR per bar
                    delta = 0.50
                    theta_decay = 0.04 * atr * holding_days
                    opt_pnl_pts = max(-risk * delta, (spot_pnl_pts * delta) - theta_decay)
                    opt_pnl_rupees = opt_pnl_pts * lot_size

                    outcome = "WIN" if spot_pnl_pts > (0.1 * risk) else ("BE" if abs(spot_pnl_pts) <= (0.1 * risk) else "LOSS")

                    all_trades.append({
                        "ticker": ticker,
                        "direction": direction,
                        "entry_date": active_trade['entry_date'],
                        "exit_date": c_date,
                        "entry": entry,
                        "exit": exit_price,
                        "sl": active_trade['orig_sl'],
                        "t1": t1,
                        "holding_days": holding_days,
                        "exit_reason": exit_reason,
                        "outcome": outcome,
                        "spot_pnl_pts": spot_pnl_pts,
                        "spot_pnl_pct": spot_pnl_pct,
                        "opt_pnl_pts": opt_pnl_pts,
                        "opt_pnl_rupees": opt_pnl_rupees,
                        "lot_size": lot_size
                    })
                    active_trade = None
                    continue

            # -----------------------------------------------------------------
            # 2. SIGNAL GENERATION & BO TRAP SUPPRESSION
            # -----------------------------------------------------------------
            if active_trade is None:
                sma200 = bar['sma200']
                sma50 = bar['sma50']
                vol_sma = bar['vol_sma']
                c_vol = bar['vol']

                is_bull_regime = (c_close >= sma200) if not np.isnan(sma200) else True
                is_bear_regime = (c_close <= sma200 and sma50 <= sma200) if not np.isnan(sma200) and not np.isnan(sma50) else False

                has_volume = c_vol >= (vol_mult * vol_sma) if not np.isnan(vol_sma) else True

                d_top = bar['demand_top']
                d_bot = bar['demand_bot']
                s_top = bar['supply_top']
                s_bot = bar['supply_bot']

                # --- 🟢 SETUP 1: DEMAND 1 RETEST & BO-UP ---
                if mode in ["BOTH", "LONG_ONLY"] and is_bull_regime:
                    in_demand = (c_low <= d_top) and (c_close >= d_bot)
                    demand_rejection = (bar['lower_wick'] >= wick_thresh) and (bar['upper_wick'] <= trap_thresh) and (c_close >= c_open)

                    # BO Trap Filter: Block BUY if close is trapped right under Supply 1
                    is_trapped_under_supply = (s_bot > c_close) and (((s_bot - c_close) / c_close) < 0.006)

                    if in_demand and demand_rejection and has_volume:
                        if use_bo_filter and is_trapped_under_supply:
                            pass # Blocked trap!
                        else:
                            entry = c_close
                            sl = min(c_low, d_bot) - (0.3 * atr)
                            risk = entry - sl
                            if risk < (min_atr_risk * atr):
                                sl = entry - (min_atr_risk * atr)
                                risk = entry - sl
                            elif risk > (max_atr_risk * atr):
                                sl = entry - (max_atr_risk * atr)
                                risk = entry - sl

                            t1 = entry + (rr_target * risk)
                            active_trade = {
                                "direction": "LONG",
                                "entry": entry,
                                "orig_sl": sl,
                                "sl": sl,
                                "t1": t1,
                                "risk": risk,
                                "holding": 0,
                                "is_be": False,
                                "entry_date": c_date
                            }
                            continue

                # --- 🔴 SETUP 2: SUPPLY 1 RETEST & BD-DOWN ---
                if mode in ["BOTH", "SHORT_ONLY"] and is_bear_regime:
                    in_supply = (c_high >= s_bot) and (c_close <= s_top)
                    supply_rejection = (bar['upper_wick'] >= wick_thresh) and (bar['lower_wick'] <= trap_thresh) and (c_close <= c_open)

                    is_trapped_above_demand = (c_close > d_top) and (((c_close - d_top) / c_close) < 0.006)

                    if in_supply and supply_rejection and has_volume:
                        if use_bo_filter and is_trapped_above_demand:
                            pass # Blocked trap!
                        else:
                            entry = c_close
                            sl = max(c_high, s_top) + (0.3 * atr)
                            risk = sl - entry
                            if risk < (min_atr_risk * atr):
                                sl = entry + (min_atr_risk * atr)
                                risk = sl - entry
                            elif risk > (max_atr_risk * atr):
                                sl = entry + (max_atr_risk * atr)
                                risk = sl - entry

                            t1 = entry - (rr_target * risk)
                            active_trade = {
                                "direction": "SHORT",
                                "entry": entry,
                                "orig_sl": sl,
                                "sl": sl,
                                "t1": t1,
                                "risk": risk,
                                "holding": 0,
                                "is_be": False,
                                "entry_date": c_date
                            }
                            continue

    return all_trades


def calculate_metrics(trades):
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "be": 0,
            "win_rate": 0.0, "profit_factor": 0.0, "net_return_pct": 0.0,
            "realized_rr": 0.0, "max_drawdown": 0.0, "opt_pnl_rs": 0.0,
            "opt_profit_factor": 0.0, "score": 0.0
        }

    total = len(trades)
    wins = [t for t in trades if t['outcome'] == "WIN"]
    losses = [t for t in trades if t['outcome'] == "LOSS"]
    bes = [t for t in trades if t['outcome'] == "BE"]

    w_cnt = len(wins)
    l_cnt = len(losses)
    be_cnt = len(bes)

    win_rate = (w_cnt / (w_cnt + l_cnt) * 100.0) if (w_cnt + l_cnt) > 0 else 0.0

    gross_profit = sum(t['spot_pnl_pct'] for t in wins)
    gross_loss = abs(sum(t['spot_pnl_pct'] for t in losses))
    net_return_pct = gross_profit - gross_loss

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 99.0
    avg_win = (gross_profit / w_cnt) if w_cnt > 0 else 0.0
    avg_loss = (gross_loss / l_cnt) if l_cnt > 0 else 0.0
    realized_rr = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    # Options Metrics
    opt_gross_profit = sum(t['opt_pnl_rupees'] for t in wins if t['opt_pnl_rupees'] > 0)
    opt_gross_loss = abs(sum(t['opt_pnl_rupees'] for t in losses if t['opt_pnl_rupees'] < 0))
    opt_net_pnl_rs = sum(t['opt_pnl_rupees'] for t in trades)
    opt_profit_factor = (opt_gross_profit / opt_gross_loss) if opt_gross_loss > 0 else 99.0

    # Drawdown calculation
    equity = 100.0
    peak = 100.0
    max_dd = 0.0
    for t in trades:
        equity += t['spot_pnl_pct']
        if equity > peak:
            peak = equity
        dd = ((peak - equity) / peak * 100.0) if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # Institutional Score: Profit Factor * Win Rate * Drawdown Suppression * Realized RR
    dd_factor = max(0.1, (1.0 - (max_dd / 150.0)))
    score = round(profit_factor * (win_rate / 100.0) * realized_rr * dd_factor * 100.0, 2)

    return {
        "total_trades": total,
        "wins": w_cnt,
        "losses": l_cnt,
        "be": be_cnt,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "net_return_pct": round(net_return_pct, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "realized_rr": round(realized_rr, 2),
        "max_drawdown": round(max_dd, 2),
        "opt_pnl_rs": round(opt_net_pnl_rs, 2),
        "opt_profit_factor": round(opt_profit_factor, 2),
        "score": score
    }


# ---------------------------------------------------------------------------
# 3. GRID SEARCH OPTIMIZATION HARNESS
# ---------------------------------------------------------------------------

def run_grid_optimization(dataset):
    print("\n" + "=" * 80)
    print("🔬 RUNNING MULTI-DIMENSIONAL VYUHA-BO PARAMETER GRID OPTIMIZATION")
    print("=" * 80)

    # 1. Baseline Run (Historical Default)
    baseline_params = {
        "rr_target": 2.0,
        "be_trigger_rr": 1.0,
        "min_atr_risk": 1.2,
        "max_atr_risk": 2.8,
        "wick_thresh": 0.25,
        "trap_thresh": 0.30,
        "vol_mult": 0.75,
        "max_holding": 20,
        "use_bo_filter": False
    }

    baseline_trades = simulate_strategy(dataset, baseline_params)
    baseline_metrics = calculate_metrics(baseline_trades)

    print(f"\n📊 BASELINE (UNOPTIMIZED) METRICS:")
    print(f"  • Total Trades       : {baseline_metrics['total_trades']}")
    print(f"  • Win Rate           : {baseline_metrics['win_rate']}% ({baseline_metrics['wins']}W / {baseline_metrics['losses']}L / {baseline_metrics['be']}BE)")
    print(f"  • Profit Factor      : {baseline_metrics['profit_factor']}")
    print(f"  • Realized R:R       : {baseline_metrics['realized_rr']}")
    print(f"  • Net Spot Return    : {baseline_metrics['net_return_pct']:+.2f}%")
    print(f"  • Max Drawdown       : {baseline_metrics['max_drawdown']:.2f}%")
    print(f"  • Options Net PnL    : Rs. {baseline_metrics['opt_pnl_rs']:+,.0f}")
    print(f"  • Options Profit Fac : {baseline_metrics['opt_profit_factor']}")
    print(f"  • Institutional Score: {baseline_metrics['score']}")

    # 2. Grid Search Space
    grid = {
        "rr_target": [1.8, 2.0, 2.2, 2.5],
        "be_trigger_rr": [1.0, 1.2, 999.0], # 1.0R trail, 1.2R trail, or No BE trail
        "min_atr_risk": [1.2, 1.5],
        "wick_thresh": [0.25, 0.28],
        "vol_mult": [0.85, 1.0],
        "max_holding": [12, 15],
        "use_bo_filter": [True] # Enforce Vyuha-BO trap suppression
    }

    keys, values = zip(*grid.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    print(f"\n🔍 Exploring {len(combinations)} systematic parameter configurations in parallel (ThreadPool 8)...")

    results = []
    start_time = time.time()

    def _eval_combination(p):
        trades = simulate_strategy(dataset, p)
        m = calculate_metrics(trades)
        if m['total_trades'] >= 80:
            return (m['score'], m, p, trades)
        return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        eval_results = executor.map(_eval_combination, combinations)
        for r in eval_results:
            if r is not None:
                results.append(r)

    elapsed = time.time() - start_time
    print(f"✓ Grid evaluation complete in {elapsed:.2f} seconds ({len(results)} valid configurations tested).")

    # Sort by Institutional Score descending
    results.sort(key=lambda x: x[0], reverse=True)
    best_score, best_metrics, best_params, best_trades = results[0]

    print("\n" + "=" * 80)
    print("🏆 BEST OPTIMIZED PARAMETER CONFIGURATION")
    print("=" * 80)
    print(json.dumps(best_params, indent=2))

    print(f"\n📈 OPTIMIZED PERFORMANCE SUMMARY:")
    print(f"  • Total Trades       : {best_metrics['total_trades']}")
    print(f"  • Win Rate           : {best_metrics['win_rate']}% (vs {baseline_metrics['win_rate']}%)")
    print(f"  • Profit Factor      : {best_metrics['profit_factor']} (vs {baseline_metrics['profit_factor']})")
    print(f"  • Realized R:R       : {best_metrics['realized_rr']} (vs {baseline_metrics['realized_rr']})")
    print(f"  • Net Spot Return    : {best_metrics['net_return_pct']:+.2f}% (vs {baseline_metrics['net_return_pct']:+.2f}%)")
    print(f"  • Max Drawdown       : {best_metrics['max_drawdown']:.2f}% (vs {baseline_metrics['max_drawdown']:.2f}%)")
    print(f"  • Options Net PnL    : Rs. {best_metrics['opt_pnl_rs']:+,.0f} (vs Rs. {baseline_metrics['opt_pnl_rs']:+,.0f})")
    print(f"  • Options Profit Fac : {best_metrics['opt_profit_factor']} (vs {baseline_metrics['opt_profit_factor']})")
    print(f"  • Institutional Score: {best_score} (vs {baseline_metrics['score']})")

    # Save audited results
    results_payload = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S") if 'datetime' in globals() else time.strftime("%Y-%m-%d %H:%M:%S"),
        "universe_size": len(dataset),
        "baseline": baseline_metrics,
        "baseline_params": baseline_params,
        "optimized": best_metrics,
        "optimized_params": best_params,
        "top_5_configurations": [
            {"rank": i + 1, "score": r[0], "metrics": r[1], "params": r[2]}
            for i, r in enumerate(results[:5])
        ]
    }

    opt_results_file = os.path.join(DATA_DIR, "backtest_vyuha_bo_optimized.json")
    with open(opt_results_file, "w") as f:
        json.dump(results_payload, f, indent=2)
    print(f"\n💾 Saved full optimization results to: {opt_results_file}")

    # Export trade log
    trades_df = pd.DataFrame(best_trades)
    opt_csv_file = os.path.join(DATA_DIR, "backtest_vyuha_bo_trades.csv")
    trades_df.to_csv(opt_csv_file, index=False)
    print(f"💾 Saved detailed trade-by-trade log to: {opt_csv_file}")

    return baseline_metrics, best_metrics, best_params, results_payload


if __name__ == "__main__":
    dataset = load_precomputed_dataset()
    if dataset:
        run_grid_optimization(dataset)
