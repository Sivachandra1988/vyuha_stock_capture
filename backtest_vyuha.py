"""
=============================================================================
🏛️ UMX VYUHA — Institutional MTF Confluence Strategy Backtest Engine
Backtests the institutional confluence, zone bounce/rejection, anti-trap wicks,
AVWAP diamond, runaway momentum breakouts, and 1:2.2 R:R trailing break-even trade engine.
=============================================================================
"""

import os
import sys
import json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

# Windows encoding safeguard
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "backtest_cache")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from scanner import core, vyuha

# Validated Institutional Parameters (Grid-Optimized for Maximum Profitability)
PARAMS = {
    "rr_target": 2.2,
    "be_trigger_rr": 1.0,
    "min_atr_risk": 1.5,
    "max_atr_risk": 2.5,
    "wick_thresh": 0.28,
    "trap_thresh": 0.25,
    "use_200_sma": True,
    "include_momentum": True,
    "max_holding": 15
}

def backtest_ticker_vyuha(ticker, period="2y", params=PARAMS):
    """
    Simulates VYUHA Institutional Confluence trading on historical daily bars.
    Strictly prevents lookahead bias: zones and levels are evaluated on rolling lookback windows.
    """
    parquet_path = os.path.join(CACHE_DIR, f"{ticker}.parquet")
    if os.path.exists(parquet_path):
        try:
            df = pd.read_parquet(parquet_path)
        except Exception:
            df = pd.DataFrame()
    else:
        try:
            df = yf.download(f"{ticker}.NS", period=period, interval="1d", progress=False, auto_adjust=True)
            if not df.empty and len(df) > 50:
                df.to_parquet(parquet_path)
        except Exception:
            df = pd.DataFrame()

    if df.empty or len(df) < 120:
        return []

    # Flatten columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col in df.columns:
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

    highs = df['High'].values
    lows = df['Low'].values
    opens = df['Open'].values
    closes = df['Close'].values
    volumes = df['Volume'].values
    atrs = df['ATR'].values
    sma200s = df['SMA200'].values
    sma50s = df['SMA50'].values
    vol_smas = df['VolSMA20'].values
    dates = df.index.strftime("%Y-%m-%d").values

    n = len(df)
    active_trade = None
    trades = []

    rr_target = params["rr_target"]
    be_trigger_rr = params["be_trigger_rr"]
    min_atr_risk = params["min_atr_risk"]
    max_atr_risk = params["max_atr_risk"]
    wick_thresh = params["wick_thresh"]
    trap_thresh = params["trap_thresh"]
    max_holding = params["max_holding"]

    for i in range(100, n):
        c_open = opens[i]
        c_high = highs[i]
        c_low = lows[i]
        c_close = closes[i]
        c_vol = volumes[i]
        atr = atrs[i]
        sma200 = sma200s[i]
        sma50 = sma50s[i]
        vol_sma = vol_smas[i]
        c_date = dates[i]

        # -----------------------------------------------------------------
        # 1. TRADE MANAGEMENT (If already in an active trade)
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
                # Check Break-Even trigger
                if c_high >= (entry + be_trigger_rr * risk) and not is_be:
                    active_trade['sl'] = entry + (0.05 * risk)
                    active_trade['is_be'] = True
                    sl = active_trade['sl']

                # Target check (1:2.2 R:R)
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
                # Check Break-Even trigger
                if c_low <= (entry - be_trigger_rr * risk) and not is_be:
                    active_trade['sl'] = entry - (0.05 * risk)
                    active_trade['is_be'] = True
                    sl = active_trade['sl']

                # Target check (1:2.2 R:R)
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
                trades.append({
                    "ticker": ticker,
                    "direction": direction,
                    "entry_date": active_trade['entry_date'],
                    "exit_date": c_date,
                    "entry_price": round(entry, 2),
                    "exit_price": round(exit_price, 2),
                    "exit_reason": exit_reason,
                    "outcome": outcome,
                    "pnl_pts": round(pnl_pts, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "holding_bars": active_trade['holding'],
                    "setup": active_trade['setup']
                })
                active_trade = None
                continue

        # -----------------------------------------------------------------
        # 2. SIGNAL GENERATION
        # -----------------------------------------------------------------
        if active_trade is None and atr > 0:
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

            # Trend Regimes
            is_bull_regime = (c_close >= sma200) if not np.isnan(sma200) else True
            is_bear_regime = (c_close <= sma200 and sma50 <= sma200) if not np.isnan(sma200) and not np.isnan(sma50) else False

            has_volume = c_vol >= (0.75 * vol_sma) if not np.isnan(vol_sma) else True

            # SETUP 1: DEMAND 1 RETEST + ANTI-TRAP (CHARTS 1 & 4)
            in_demand = (c_low <= demand_top) and (c_close >= demand_bot)
            demand_rejection = (lower_wick_ratio >= wick_thresh) and (upper_wick_ratio <= trap_thresh) and (c_close >= c_open)

            if is_bull_regime and in_demand and demand_rejection and has_volume:
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

            # SETUP 2: SUPPLY 1 RETEST + ANTI-TRAP (CHARTS 2 & 3)
            in_supply = (c_high >= supply_bot) and (c_close <= supply_top)
            supply_rejection = (upper_wick_ratio >= wick_thresh) and (lower_wick_ratio <= trap_thresh) and (c_close <= c_open)

            if is_bear_regime and in_supply and supply_rejection and has_volume:
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

            # SETUP 3: RUNAWAY MOMENTUM BREAKOUT (NEWS EXPANSION)
            if params["include_momentum"] and is_bull_regime:
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

    return trades

def run_full_vyuha_backtest(tickers=None):
    if not tickers:
        tickers = core.get_dynamic_tickers()
        if not tickers:
            tickers = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "LT", "BAJFINANCE"]

    print("\n" + "=" * 78)
    print(f"🏛️  UMX VYUHA INSTITUTIONAL CONFLUENCE & PRICE ACTION BACKTEST")
    print(f"Universe: {len(tickers)} Stocks | Period: 2 Years Historical Daily Bars")
    print(f"Rules: Zone 1 Retest | Anti-Trap Wicks (Charts 1-4) | 1:2.2 R:R Trailing BE")
    print("=" * 78 + "\n")

    all_trades = []
    stock_summaries = []

    for ticker in tickers:
        t_list = backtest_ticker_vyuha(ticker, period="2y")
        if t_list:
            all_trades.extend(t_list)
            w = len([t for t in t_list if t['outcome'] == 'WIN'])
            l = len([t for t in t_list if t['outcome'] == 'LOSS'])
            be = len([t for t in t_list if t['outcome'] == 'BE'])
            total = len(t_list)
            wr = (w / (w + l) * 100.0) if (w + l) > 0 else 0.0
            pnl = sum(t['pnl_pct'] for t in t_list)
            stock_summaries.append((ticker, total, w, l, be, wr, pnl))
            print(f"  ✓ {ticker:<12} : {total:2d} Trades | {w:2d}W {l:2d}L {be:2d}BE | Win Rate: {wr:5.1f}% | Net PnL: {pnl:+6.1f}%")

    if not all_trades:
        print("[INFO] No trades generated.")
        return

    total_trades = len(all_trades)
    wins = [t for t in all_trades if t['outcome'] == 'WIN']
    losses = [t for t in all_trades if t['outcome'] == 'LOSS']
    break_evens = [t for t in all_trades if t['outcome'] == 'BE']

    win_count = len(wins)
    loss_count = len(losses)
    be_count = len(break_evens)

    win_rate = (win_count / (win_count + loss_count) * 100.0) if (win_count + loss_count) > 0 else 0.0
    overall_win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

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

    # Setup Breakdown
    setups = ["DEMAND_CONFLUENCE_RETEST", "SUPPLY_CONFLUENCE_RETEST", "RUNAWAY_MOMENTUM_BREAKOUT"]

    print("\n" + "=" * 78)
    print("📊 PERFORMANCE BREAKDOWN BY STRATEGY SETUP")
    print("=" * 78)
    print(f"  {'SETUP NAME':<28} {'TRADES':<8} {'WIN RATE':<10} {'NET PNL':<12} {'PROFIT FACTOR':<10}")
    print("  " + "-" * 74)
    for s_name in setups:
        st = [t for t in all_trades if t['setup'] == s_name]
        if st:
            sw = len([t for t in st if t['outcome'] == 'WIN'])
            sl = len([t for t in st if t['outcome'] == 'LOSS'])
            s_wr = (sw / (sw + sl) * 100.0) if (sw + sl) > 0 else 0.0
            s_pnl = sum(t['pnl_pct'] for t in st)
            s_gw = sum(t['pnl_pct'] for t in st if t['outcome'] == 'WIN')
            s_gl = abs(sum(t['pnl_pct'] for t in st if t['outcome'] == 'LOSS'))
            s_pf = (s_gw / s_gl) if s_gl > 0 else 99.0
            print(f"  {s_name:<28} {len(st):<8d} {s_wr:5.1f}%     {s_pnl:+7.1f}%      {s_pf:4.2f}")

    print("\n" + "=" * 78)
    print("🏛️  OVERALL PORTFOLIO AUDIT METRICS (2-YEAR SIMULATION)")
    print("=" * 78)
    print(f"  Total Trades Executed:    {total_trades}")
    print(f"  Wins / Losses / BE:       {win_count} Wins | {loss_count} Losses | {be_count} Break-Evens")
    print(f"  Win Rate (Excluding BE):  {win_rate:.2f}%  (Overall: {overall_win_rate:.2f}%)")
    print(f"  Profit Factor:            {profit_factor:.2f}")
    print(f"  Realized Risk:Reward:     1:{realized_rr:.2f}")
    print(f"  Average Win / Loss:       +{avg_win:.2f}% / -{avg_loss:.2f}%")
    print(f"  Cumulative Net Return:    +{net_return:.2f}%")
    print(f"  Maximum Drawdown:         {max_dd:.2f}%")
    print("=" * 78 + "\n")

    # Save to JSON
    output_file = os.path.join(DATA_DIR, "vyuha_backtest_results.json")
    results_payload = {
        "metrics": {
            "total_trades": total_trades,
            "wins": win_count,
            "losses": loss_count,
            "break_evens": be_count,
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "net_return_pct": round(net_return, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "realized_rr": round(realized_rr, 2),
            "max_drawdown": round(max_dd, 2)
        },
        "trades": all_trades
    }
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results_payload, f, indent=4)
    print(f"📁 Full trade log saved to: {output_file}\n")

if __name__ == "__main__":
    run_full_vyuha_backtest()
