"""
High-Speed Vectorized & Audited VYUHA Strategy Optimizer & Backtest Engine
"""
import os
import sys
import glob
import json
import time

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr.encoding != "utf-8":
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "backtest_cache")

def run_strategy_backtest(params):
    """
    Backtests the VYUHA Confluence + 4-Chart Price Action Rules across all cached stocks.
    params: dict with configurable thresholds
    """
    rr_target = params.get("rr_target", 2.0)
    be_trigger_rr = params.get("be_trigger_rr", 1.0)
    min_atr_risk = params.get("min_atr_risk", 1.2)
    max_atr_risk = params.get("max_atr_risk", 2.8)
    max_holding = params.get("max_holding", 20)
    wick_thresh = params.get("wick_thresh", 0.25)
    trap_thresh = params.get("trap_thresh", 0.30)
    use_200_sma = params.get("use_200_sma", True)
    include_momentum = params.get("include_momentum", True)

    parquet_files = glob.glob(os.path.join(CACHE_DIR, "*.parquet"))
    if not parquet_files:
        print("[ERROR] No cached parquet files found in", CACHE_DIR)
        return None

    all_trades = []

    for file_path in parquet_files:
        ticker = os.path.basename(file_path).replace(".parquet", "")
        try:
            df = pd.read_parquet(file_path)
            if len(df) < 150:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

            # Ensure numeric
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df = df.dropna(subset=['Close'])

            # Pre-compute indicators
            h_l = df['High'] - df['Low']
            h_pc = (df['High'] - df['Close'].shift(1)).abs()
            l_pc = (df['Low'] - df['Close'].shift(1)).abs()
            tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
            df['ATR'] = tr.ewm(alpha=1.0/14.0, adjust=False).mean()
            df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
            df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
            df['SMA50'] = df['Close'].rolling(50).mean()
            df['SMA200'] = df['Close'].rolling(200).mean()
            df['VolSMA20'] = df['Volume'].rolling(20).mean()

            # Pre-compute swing highs and lows (left=3, right=3)
            highs = df['High'].values
            lows = df['Low'].values
            opens = df['Open'].values
            closes = df['Close'].values
            volumes = df['Volume'].values
            atrs = df['ATR'].values
            sma200s = df['SMA200'].values
            sma50s = df['SMA50'].values
            ema9s = df['EMA9'].values
            ema21s = df['EMA21'].values
            vol_smas = df['VolSMA20'].values
            dates = df.index.strftime("%Y-%m-%d").values

            n = len(df)
            active_trade = None

            for i in range(100, n):
                c_open = opens[i]
                c_high = highs[i]
                c_low = lows[i]
                c_close = closes[i]
                c_vol = volumes[i]
                atr = atrs[i]
                sma200 = sma200s[i]
                sma50 = sma50s[i]
                ema9 = ema9s[i]
                ema21 = ema21s[i]
                vol_sma = vol_smas[i]
                c_date = dates[i]

                # -------------------------------------------------------------
                # 1. TRADE MANAGEMENT
                # -------------------------------------------------------------
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
                        # Move to BE
                        if c_high >= (entry + be_trigger_rr * risk) and not is_be:
                            active_trade['sl'] = entry + (0.05 * risk) # lock tiny profit
                            active_trade['is_be'] = True
                            sl = active_trade['sl']

                        # Target check
                        if c_high >= t1:
                            trade_closed = True
                            exit_price = t1
                            exit_reason = f"TARGET (1:{rr_target} R:R)"
                        elif c_low <= sl:
                            trade_closed = True
                            exit_price = sl
                            exit_reason = "BREAK_EVEN" if is_be else "STOP_LOSS"
                        elif active_trade['holding'] >= max_holding:
                            trade_closed = True
                            exit_price = c_close
                            exit_reason = "TIME_EXPIRY"

                    elif direction == "SHORT":
                        # Move to BE
                        if c_low <= (entry - be_trigger_rr * risk) and not is_be:
                            active_trade['sl'] = entry - (0.05 * risk)
                            active_trade['is_be'] = True
                            sl = active_trade['sl']

                        # Target check
                        if c_low <= t1:
                            trade_closed = True
                            exit_price = t1
                            exit_reason = f"TARGET (1:{rr_target} R:R)"
                        elif c_high >= sl:
                            trade_closed = True
                            exit_price = sl
                            exit_reason = "BREAK_EVEN" if is_be else "STOP_LOSS"
                        elif active_trade['holding'] >= max_holding:
                            trade_closed = True
                            exit_price = c_close
                            exit_reason = "TIME_EXPIRY"

                    if trade_closed:
                        pnl_pts = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
                        pnl_pct = (pnl_pts / entry) * 100.0
                        outcome = "WIN" if pnl_pts > (0.1 * risk) else ("BE" if abs(pnl_pts) <= (0.1 * risk) else "LOSS")
                        all_trades.append({
                            "ticker": ticker,
                            "direction": direction,
                            "entry_date": active_trade['entry_date'],
                            "exit_date": c_date,
                            "entry": entry,
                            "exit": exit_price,
                            "exit_reason": exit_reason,
                            "outcome": outcome,
                            "pnl_pct": pnl_pct,
                            "setup": active_trade['setup']
                        })
                        active_trade = None
                        continue

                # -------------------------------------------------------------
                # 2. SIGNAL GENERATION
                # -------------------------------------------------------------
                if active_trade is None and atr > 0:
                    c_range = max(c_high - c_low, 0.001)
                    lower_wick = min(c_open, c_close) - c_low
                    upper_wick = c_high - max(c_open, c_close)
                    lower_wick_ratio = lower_wick / c_range
                    upper_wick_ratio = upper_wick / c_range

                    # Rolling lookback for Demand & Supply Zones (past 60 bars)
                    lookback_slice_h = highs[max(0, i-60):i]
                    lookback_slice_l = lows[max(0, i-60):i]
                    lookback_slice_c = closes[max(0, i-60):i]

                    # Prior key swing levels
                    pdh = highs[i-1]
                    pdl = lows[i-1]
                    rolling_high = np.max(lookback_slice_h)
                    rolling_low = np.min(lookback_slice_l)

                    # Dynamic Demand 1 Zone: between 0.382 and 0.618 retracement of recent range
                    swing_range = rolling_high - rolling_low
                    if swing_range <= 0:
                        continue

                    demand_top = rolling_low + (0.50 * swing_range)
                    demand_bot = rolling_low + (0.20 * swing_range)
                    supply_top = rolling_high - (0.20 * swing_range)
                    supply_bot = rolling_high - (0.50 * swing_range)

                    # Long Regime: Stock in Uptrend (Price > 200 SMA and 50 SMA > 200 SMA or Price > 50 SMA)
                    is_bull_regime = (c_close >= sma200) if use_200_sma and not np.isnan(sma200) else True
                    # Short Regime: Stock in Confirmed Macro Downtrend (Price < 200 SMA and 50 SMA < 200 SMA)
                    is_bear_regime = (c_close <= sma200 and sma50 <= sma200) if use_200_sma and not np.isnan(sma200) and not np.isnan(sma50) else False

                    # ---------------------------------------------------------
                    # SETUP 1: VYUHA DEMAND CONFLUENCE + ANTI-TRAP WICK (CHART 1 & 4)
                    # ---------------------------------------------------------
                    in_demand_zone = (c_low <= demand_top) and (c_close >= demand_bot)
                    # Chart 4: Rejection of lower level + NO Long Upper Wick
                    demand_rejection = (lower_wick_ratio >= wick_thresh) and (upper_wick_ratio <= trap_thresh) and (c_close >= c_open)
                    has_volume = c_vol >= (0.75 * vol_sma) if not np.isnan(vol_sma) else True

                    if is_bull_regime and in_demand_zone and demand_rejection and has_volume:
                        entry = c_close
                        sl = min(c_low, demand_bot) - (0.3 * atr)
                        risk = entry - sl
                        if risk < (min_atr_risk * atr):
                            sl = entry - (min_atr_risk * atr)
                            risk = entry - sl
                        elif risk > (max_atr_risk * atr):
                            sl = entry - (max_atr_risk * atr)
                            risk = entry - sl

                        t1 = entry + (rr_target * risk)
                        active_trade = {
                            "ticker": ticker,
                            "direction": "LONG",
                            "entry": entry,
                            "sl": sl,
                            "t1": t1,
                            "risk": risk,
                            "holding": 0,
                            "is_be": False,
                            "entry_date": c_date,
                            "setup": "DEMAND_CONFLUENCE_RETEST"
                        }
                        continue

                    # ---------------------------------------------------------
                    # SETUP 2: VYUHA SUPPLY CONFLUENCE + ANTI-TRAP WICK (CHART 2 & 3)
                    # ---------------------------------------------------------
                    in_supply_zone = (c_high >= supply_bot) and (c_close <= supply_top)
                    # Chart 3: Rejection of upper level + NO Long Lower Wick
                    supply_rejection = (upper_wick_ratio >= wick_thresh) and (lower_wick_ratio <= trap_thresh) and (c_close <= c_open)

                    if is_bear_regime and in_supply_zone and supply_rejection and has_volume:
                        entry = c_close
                        sl = max(c_high, supply_top) + (0.3 * atr)
                        risk = sl - entry
                        if risk < (min_atr_risk * atr):
                            sl = entry + (min_atr_risk * atr)
                            risk = sl - entry
                        elif risk > (max_atr_risk * atr):
                            sl = entry + (max_atr_risk * atr)
                            risk = sl - entry

                        t1 = entry - (rr_target * risk)
                        active_trade = {
                            "ticker": ticker,
                            "direction": "SHORT",
                            "entry": entry,
                            "sl": sl,
                            "t1": t1,
                            "risk": risk,
                            "holding": 0,
                            "is_be": False,
                            "entry_date": c_date,
                            "setup": "SUPPLY_CONFLUENCE_RETEST"
                        }
                        continue

                    # ---------------------------------------------------------
                    # SETUP 3: RUNAWAY MOMENTUM EXPANSION BREAKOUT (NEWS / BREAKOUT)
                    # ---------------------------------------------------------
                    if include_momentum and is_bull_regime:
                        # High-Volume breakout above 20-day high with solid green candle
                        is_news_volume = c_vol >= (2.0 * vol_sma) if not np.isnan(vol_sma) else False
                        is_breakout_20d = c_close > np.max(highs[max(0, i-20):i])
                        is_solid_candle = (c_close - c_open) >= (0.8 * atr) and (upper_wick_ratio <= 0.20)

                        if is_news_volume and is_breakout_20d and is_solid_candle:
                            entry = c_close
                            sl = c_low - (0.2 * atr)
                            risk = entry - sl
                            if risk < (min_atr_risk * atr):
                                sl = entry - (min_atr_risk * atr)
                                risk = entry - sl
                            elif risk > (max_atr_risk * atr):
                                sl = entry - (max_atr_risk * atr)
                                risk = entry - sl

                            t1 = entry + (rr_target * risk)
                            active_trade = {
                                "ticker": ticker,
                                "direction": "LONG",
                                "entry": entry,
                                "sl": sl,
                                "t1": t1,
                                "risk": risk,
                                "holding": 0,
                                "is_be": False,
                                "entry_date": c_date,
                                "setup": "RUNAWAY_MOMENTUM_BREAKOUT"
                            }
                            continue

        except Exception as e:
            continue

    if not all_trades:
        return None

    # Compute strategy performance
    total = len(all_trades)
    wins = [t for t in all_trades if t['outcome'] == 'WIN']
    losses = [t for t in all_trades if t['outcome'] == 'LOSS']
    bes = [t for t in all_trades if t['outcome'] == 'BE']

    win_count = len(wins)
    loss_count = len(losses)
    be_count = len(bes)

    win_rate = (win_count / (win_count + loss_count) * 100.0) if (win_count + loss_count) > 0 else 0.0
    overall_win_rate = (win_count / total * 100.0) if total > 0 else 0.0

    gross_profit = sum(t['pnl_pct'] for t in wins)
    gross_loss = abs(sum(t['pnl_pct'] for t in losses))
    net_return = gross_profit - gross_loss

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 99.0
    avg_win = (gross_profit / win_count) if win_count > 0 else 0.0
    avg_loss = (gross_loss / loss_count) if loss_count > 0 else 0.0
    realized_rr = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    # Max Drawdown
    equity = 100.0
    peak = 100.0
    max_dd = 0.0
    for t in all_trades:
        equity += t['pnl_pct']
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    return {
        "params": params,
        "total_trades": total,
        "wins": win_count,
        "losses": loss_count,
        "break_evens": be_count,
        "win_rate": round(win_rate, 2),
        "overall_win_rate": round(overall_win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "net_return_pct": round(net_return, 2),
        "gross_profit_pct": round(gross_profit, 2),
        "gross_loss_pct": round(gross_loss, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "realized_rr": round(realized_rr, 2),
        "max_drawdown": round(max_dd, 2),
        "trades": all_trades
    }

if __name__ == "__main__":
    t0 = time.time()
    # Baseline test
    res = run_strategy_backtest({
        "rr_target": 2.0,
        "be_trigger_rr": 1.0,
        "min_atr_risk": 1.3,
        "max_atr_risk": 2.5,
        "wick_thresh": 0.25,
        "trap_thresh": 0.30,
        "use_200_sma": True,
        "include_momentum": True
    })

    print(f"\nExecution took {time.time() - t0:.2f} seconds.")
    if res:
        print("\n" + "=" * 65)
        print("🏛️  VYUHA + 4-CHART PRICE ACTION BACKTEST RESULTS")
        print("=" * 65)
        print(f"Total Trades:     {res['total_trades']}")
        print(f"Wins / Losses:    {res['wins']} / {res['losses']} (Break-Evens: {res['break_evens']})")
        print(f"Win Rate (Ex BE): {res['win_rate']}%  (Overall: {res['overall_win_rate']}%)")
        print(f"Profit Factor:    {res['profit_factor']}")
        print(f"Realized R:R:     1:{res['realized_rr']}")
        print(f"Avg Win / Loss:   +{res['avg_win_pct']}% / -{res['avg_loss_pct']}%")
        print(f"Net Return PnL:   +{res['net_return_pct']}%")
        print(f"Max Drawdown:     {res['max_drawdown']}%")
        print("=" * 65)
