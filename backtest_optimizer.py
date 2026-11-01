"""
Fast VYUHA Backtest Optimizer using cached parquets.
Tests different execution gates and risk-reward targets to maximize Profit Factor and Win Rate.
"""

import os, sys, glob
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from scanner import vyuha

CACHE_DIR = os.path.join(BASE_DIR, "data", "backtest_cache")

def load_cached_universe():
    dfs = {}
    for p in glob.glob(os.path.join(CACHE_DIR, "*.parquet")):
        ticker = os.path.basename(p).replace(".parquet", "")
        df = pd.read_parquet(p)
        df = vyuha.clean_dataframe(df)
        if len(df) >= 150:
            df['ATR_14'] = vyuha.calculate_atr(df, 14)
            df['EMA_9'] = vyuha.calculate_ema(df['Close'], 9)
            df['EMA_21'] = vyuha.calculate_ema(df['Close'], 21)
            df['SMA_50'] = df['Close'].rolling(50).mean()
            df['SMA_200'] = df['Close'].rolling(200).mean()
            df['Vol_SMA_20'] = df['Volume'].rolling(20).mean()
            dfs[ticker] = df
    return dfs

def simulate_strategy(dfs, params):
    """
    params:
      mode: 'LONG_ONLY' or 'BOTH'
      rr_target: float (e.g. 1.5, 2.0)
      use_200_sma: bool
      min_wick_ratio: float (e.g. 0.28, 0.35)
      vol_mult: float (e.g. 0.9, 1.1)
      partial_exit: bool (take 50% profit at 1.2R)
    """
    mode = params.get('mode', 'BOTH')
    rr_target = params.get('rr_target', 2.0)
    use_200_sma = params.get('use_200_sma', True)
    min_wick_ratio = params.get('min_wick_ratio', 0.28)
    vol_mult = params.get('vol_mult', 0.9)
    max_holding = params.get('max_holding', 15)

    all_trades = []

    for ticker, df in dfs.items():
        active_trade = None

        for i in range(120, len(df)):
            curr_bar = df.iloc[i]
            prev_bar = df.iloc[i - 1]
            c_date = df.index[i].strftime("%Y-%m-%d")
            c_open = float(curr_bar['Open'])
            c_high = float(curr_bar['High'])
            c_low = float(curr_bar['Low'])
            c_close = float(curr_bar['Close'])
            atr_val = float(curr_bar['ATR_14'])

            # 1. Manage Active Trade
            if active_trade is not None:
                active_trade['holding_bars'] += 1
                entry_p = active_trade['entry_price']
                risk = active_trade['risk']
                t1_p = active_trade['target_1']
                direction = active_trade['direction']

                trade_closed = False
                exit_price = None
                exit_reason = None

                if direction == "LONG":
                    # Check 1:1 Trailing Stop
                    if c_high >= (entry_p + risk) and not active_trade['is_be']:
                        active_trade['stop_loss'] = entry_p
                        active_trade['is_be'] = True

                    if c_high >= t1_p:
                        trade_closed = True
                        exit_price = t1_p
                        exit_reason = "TARGET"
                    elif c_low <= active_trade['stop_loss']:
                        trade_closed = True
                        exit_price = active_trade['stop_loss']
                        exit_reason = "BE" if active_trade['is_be'] else "SL"
                    elif active_trade['holding_bars'] >= max_holding:
                        trade_closed = True
                        exit_price = c_close
                        exit_reason = "TIME"

                elif direction == "SHORT":
                    # Check 1:1 Trailing Stop
                    if c_low <= (entry_p - risk) and not active_trade['is_be']:
                        active_trade['stop_loss'] = entry_p
                        active_trade['is_be'] = True

                    if c_low <= t1_p:
                        trade_closed = True
                        exit_price = t1_p
                        exit_reason = "TARGET"
                    elif c_high >= active_trade['stop_loss']:
                        trade_closed = True
                        exit_price = active_trade['stop_loss']
                        exit_reason = "BE" if active_trade['is_be'] else "SL"
                    elif active_trade['holding_bars'] >= max_holding:
                        trade_closed = True
                        exit_price = c_close
                        exit_reason = "TIME"

                if trade_closed:
                    pnl_pts = (exit_price - entry_p) if direction == "LONG" else (entry_p - exit_price)
                    pnl_pct = (pnl_pts / entry_p) * 100.0
                    outcome = "WIN" if pnl_pts > (0.1 * risk) else ("BE" if abs(pnl_pts) <= (0.1 * risk) else "LOSS")

                    all_trades.append({
                        "ticker": ticker,
                        "direction": direction,
                        "entry_date": active_trade['entry_date'],
                        "entry_price": entry_p,
                        "exit_date": c_date,
                        "exit_price": exit_price,
                        "outcome": outcome,
                        "pnl_pct": pnl_pct,
                        "exit_reason": exit_reason
                    })
                    active_trade = None
                    continue

            # 2. Generate Signals
            if active_trade is None and atr_val > 0:
                hist_df = df.iloc[:i]
                zones = vyuha.calculate_vyuha_zones(hist_df, c_close, atr_val)
                if not zones or zones['is_squeeze']:
                    continue

                d1 = zones['demand_1']
                s1 = zones['supply_1']

                is_bull_trap, is_bear_trap, lower_wick_pct, upper_wick_pct = vyuha.evaluate_anti_trap_gates(curr_bar)

                # Moving averages & Volume
                sma50 = float(curr_bar['SMA_50']) if not pd.isna(curr_bar['SMA_50']) else c_close
                sma200 = float(curr_bar['SMA_200']) if not pd.isna(curr_bar['SMA_200']) else sma50
                vol = float(curr_bar['Volume'])
                vol_sma = float(curr_bar['Vol_SMA_20']) if not pd.isna(curr_bar['Vol_SMA_20']) else vol
                has_vol = vol >= (vol_mult * vol_sma)

                # Regime Filters
                if use_200_sma:
                    bull_regime = (c_close >= sma200) and (sma50 >= sma200)
                    bear_regime = (c_close <= sma200) and (sma50 <= sma200)
                else:
                    bull_regime = c_close >= sma50
                    bear_regime = c_close <= sma50

                # LONG: STRICT Zone 1 Demand Rejection
                in_demand = (c_low <= d1['top'] and c_close >= d1['bot'])
                demand_rejection = lower_wick_pct >= min_wick_ratio and c_close > c_open

                if bull_regime and has_vol and in_demand and demand_rejection and not is_bull_trap:
                    entry = c_close
                    sl = zones['dem_retrace']['sl']
                    risk = entry - sl
                    if risk <= 0 or risk > (2.5 * atr_val):
                        sl = entry - (1.2 * atr_val)
                        risk = entry - sl

                    t1 = entry + (rr_target * risk)
                    active_trade = {
                        "ticker": ticker,
                        "direction": "LONG",
                        "entry_date": c_date,
                        "entry_price": entry,
                        "stop_loss": sl,
                        "target_1": t1,
                        "risk": risk,
                        "holding_bars": 0,
                        "is_be": False
                    }
                    continue

                # SHORT: STRICT Zone 1 Supply Rejection (Only if mode == 'BOTH')
                if mode == 'BOTH':
                    in_supply = (c_high >= s1['bot'] and c_close <= s1['top'])
                    supply_rejection = upper_wick_pct >= min_wick_ratio and c_close < c_open

                    if bear_regime and has_vol and in_supply and supply_rejection and not is_bear_trap:
                        entry = c_close
                        sl = zones['sup_retrace']['sl']
                        risk = sl - entry
                        if risk <= 0 or risk > (2.5 * atr_val):
                            sl = entry + (1.2 * atr_val)
                            risk = sl - entry

                        t1 = entry - (rr_target * risk)
                        active_trade = {
                            "ticker": ticker,
                            "direction": "SHORT",
                            "entry_date": c_date,
                            "entry_price": entry,
                            "stop_loss": sl,
                            "target_1": t1,
                            "risk": risk,
                            "holding_bars": 0,
                            "is_be": False
                        }
                        continue

    # Calculate Metrics
    if not all_trades:
        return {"total": 0, "win_rate": 0, "pf": 0, "pnl": 0}

    total = len(all_trades)
    wins = [t for t in all_trades if t['outcome'] == 'WIN']
    losses = [t for t in all_trades if t['outcome'] == 'LOSS']
    bes = [t for t in all_trades if t['outcome'] == 'BE']

    w = len(wins)
    l = len(losses)
    be = len(bes)

    wr = (w / (w + l) * 100.0) if (w + l) > 0 else 0.0
    pnl = sum(t['pnl_pct'] for t in all_trades)
    gross_win = sum(t['pnl_pct'] for t in wins)
    gross_loss = abs(sum(t['pnl_pct'] for t in losses)) if losses else 0.001
    pf = gross_win / gross_loss if gross_loss > 0 else gross_win

    avg_w = gross_win / w if w > 0 else 0
    avg_l = gross_loss / l if l > 0 else 0

    return {
        "total": total,
        "wins": w,
        "losses": l,
        "be": be,
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "pnl": round(pnl, 1),
        "avg_win": round(avg_w, 2),
        "avg_loss": round(avg_l, 2),
        "trades": all_trades
    }

if __name__ == "__main__":
    print("Loading cached universe...")
    dfs = load_cached_universe()
    print(f"Loaded {len(dfs)} tickers successfully.\n")

    grid = [
        {"name": "1. Baseline (Both Sides, 50 SMA, 2.0R)", "mode": "BOTH", "use_200_sma": False, "rr_target": 2.0, "min_wick_ratio": 0.28, "vol_mult": 0.85},
        {"name": "2. Strict 200 SMA Regime (Both Sides, 2.0R)", "mode": "BOTH", "use_200_sma": True, "rr_target": 2.0, "min_wick_ratio": 0.28, "vol_mult": 0.9},
        {"name": "3. Strict 200 SMA Regime (Both Sides, 1.5R)", "mode": "BOTH", "use_200_sma": True, "rr_target": 1.5, "min_wick_ratio": 0.28, "vol_mult": 0.9},
        {"name": "4. Long-Only Indian Equities (200 SMA, 2.0R)", "mode": "LONG_ONLY", "use_200_sma": True, "rr_target": 2.0, "min_wick_ratio": 0.28, "vol_mult": 0.9},
        {"name": "5. Long-Only Indian Equities (200 SMA, 1.5R)", "mode": "LONG_ONLY", "use_200_sma": True, "rr_target": 1.5, "min_wick_ratio": 0.28, "vol_mult": 0.9},
        {"name": "6. High-Conviction Long-Only (200 SMA + Wick >= 32% + Vol 1.0x, 1.8R)", "mode": "LONG_ONLY", "use_200_sma": True, "rr_target": 1.8, "min_wick_ratio": 0.32, "vol_mult": 1.0},
        {"name": "7. High-Conviction Long-Only (200 SMA + Wick >= 30% + Vol 0.95x, 1.6R)", "mode": "LONG_ONLY", "use_200_sma": True, "rr_target": 1.6, "min_wick_ratio": 0.30, "vol_mult": 0.95},
    ]

    print(f"{'Configuration':<45} {'Trades':<8} {'Wins':<6} {'Loss':<6} {'BE':<6} {'Win Rate':<10} {'PF':<7} {'PnL %':<10}")
    print("-" * 100)

    for g in grid:
        res = simulate_strategy(dfs, g)
        print(f"{g['name']:<45} {res['total']:<8} {res['wins']:<6} {res['losses']:<6} {res['be']:<6} {res['win_rate']:>5.1f}%     {res['profit_factor']:<7.2f} {res['pnl']:>+7.1f}%")

