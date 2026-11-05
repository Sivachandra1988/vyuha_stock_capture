"""
=============================================================================
🏛️ UMX VYUHA — CAS BTST / STBT Institutional Parameter Optimization & Backtest
=============================================================================
Compares multiple parameter configurations across liquid F&O stocks:
- Configuration A: Baseline (Range >= 85%, CAS Vol >= 1.4x)
- Configuration B: Institutional Trend (Range >= 90%, CAS Vol >= 1.8x, Daily EMA 20 filter)
- Configuration C: Elite Vyuha Confluence (Range >= 92%, CAS Vol >= 2.2x, Daily Trend + Breakout)
=============================================================================
"""

import os
import sys
import warnings
warnings.filterwarnings("ignore")
warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=UserWarning)

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from scanner import core, vyuha

def evaluate_setup_with_params(ticker, df_15m, df_daily, min_range_pos=0.88, min_vol_ratio=1.8, trend_filter=True):
    if len(df_15m) < 15:
        return None

    last_dt = df_15m.index[-1]
    if hasattr(last_dt, 'date'):
        last_date = last_dt.date()
        df_today = df_15m[df_15m.index.date == last_date]
    else:
        df_today = df_15m.iloc[-25:]

    if len(df_today) < 4:
        df_today = df_15m.iloc[-15:]

    cmp = float(df_today['Close'].iloc[-1])
    day_open = float(df_today['Open'].iloc[0])
    day_high = float(df_today['High'].max())
    day_low = float(df_today['Low'].min())
    day_range = day_high - day_low

    if day_range <= 0 or cmp <= 0:
        return None

    range_pos = (cmp - day_low) / day_range

    typ_prices = (df_today['High'] + df_today['Low'] + df_today['Close']) / 3.0
    vols = df_today['Volume']
    total_vol = vols.sum()
    day_vwap = float((typ_prices * vols).sum() / total_vol) if total_vol > 0 else float(typ_prices.mean())

    latest_vol = float(df_15m['Volume'].iloc[-1])
    avg_20_vol = float(df_15m['Volume'].iloc[-21:-1].mean()) if len(df_15m) >= 21 else float(df_15m['Volume'].mean())
    cas_vol_ratio = latest_vol / max(1.0, avg_20_vol)

    # Daily Trend Filter
    daily_ema20 = None
    daily_rsi = None
    pdh = None
    pdl = None

    if df_daily is not None and len(df_daily) >= 20:
        d_close = df_daily['Close']
        daily_ema20 = float(d_close.ewm(span=20, adjust=False).mean().iloc[-1])
        delta = d_close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 0.0001)
        daily_rsi = float((100 - (100 / (1 + rs))).iloc[-1])
        pdh = float(df_daily['High'].iloc[-2])
        pdl = float(df_daily['Low'].iloc[-2])

    # Check BTST
    is_btst = False
    if range_pos >= min_range_pos and cmp > day_vwap and cmp > day_open and cas_vol_ratio >= min_vol_ratio:
        if trend_filter:
            if daily_ema20 and cmp < daily_ema20:
                pass # Against daily trend
            elif daily_rsi and daily_rsi < 50:
                pass # Weak momentum
            elif pdh and cmp < (pdh * 0.995):
                pass
            else:
                is_btst = True
        else:
            is_btst = True

    if is_btst:
        # Give enough breathing room for overnight gap variance (2.0% SL or Day Low)
        sl = round(max(day_low * 0.998, cmp * 0.978), 2)
        risk = cmp - sl
        t1 = round(cmp + max(1.5 * risk, cmp * 0.016), 2)
        t2 = round(cmp + max(2.8 * risk, cmp * 0.032), 2)
        return {
            "ticker": ticker,
            "direction": "BUY",
            "trade_type": "BTST",
            "entry": round(cmp, 2),
            "sl": sl,
            "target1": t1,
            "target2": t2,
            "range_pos": range_pos,
            "cas_vol_ratio": cas_vol_ratio
        }

    # Check STBT
    is_stbt = False
    if range_pos <= (1.0 - min_range_pos) and cmp < day_vwap and cmp < day_open and cas_vol_ratio >= min_vol_ratio:
        if trend_filter:
            if daily_ema20 and cmp > daily_ema20:
                pass
            elif daily_rsi and daily_rsi > 50:
                pass
            elif pdl and cmp > (pdl * 1.005):
                pass
            else:
                is_stbt = True
        else:
            is_stbt = True

    if is_stbt:
        sl = round(min(day_high * 1.002, cmp * 1.022), 2)
        risk = sl - cmp
        t1 = round(cmp - max(1.5 * risk, cmp * 0.016), 2)
        t2 = round(cmp - max(2.8 * risk, cmp * 0.032), 2)
        return {
            "ticker": ticker,
            "direction": "SELL",
            "trade_type": "STBT",
            "entry": round(cmp, 2),
            "sl": sl,
            "target1": t1,
            "target2": t2,
            "range_pos": range_pos,
            "cas_vol_ratio": cas_vol_ratio
        }

    return None

def run_simulation(config_name, min_range_pos, min_vol_ratio, trend_filter, data_15m, data_daily, symbols):
    trade_log = []

    for symbol in symbols:
        ticker_ns = symbol + ".NS"
        if ticker_ns not in data_15m.columns.levels[0]:
            continue

        df_sym_15m = data_15m[ticker_ns].dropna()
        df_sym_daily = data_daily[ticker_ns].dropna() if data_daily is not None and ticker_ns in data_daily.columns.levels[0] else None

        if len(df_sym_15m) < 50:
            continue

        try:
            if df_sym_15m.index.tz is None:
                df_sym_15m.index = df_sym_15m.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
            else:
                df_sym_15m.index = df_sym_15m.index.tz_convert("Asia/Kolkata")
        except Exception:
            pass

        dates = sorted(list(set(df_sym_15m.index.date)))
        if len(dates) < 5:
            continue

        for d_idx in range(len(dates) - 1):
            eval_date = dates[d_idx]
            next_date = dates[d_idx + 1]

            day_bars = df_sym_15m[df_sym_15m.index.date == eval_date]
            if len(day_bars) < 10:
                continue

            cas_bars = day_bars[(day_bars.index.hour == 15) & (day_bars.index.minute >= 0)]
            cas_bar_idx = df_sym_15m.index.get_loc(cas_bars.index[-1]) if len(cas_bars) > 0 else df_sym_15m.index.get_loc(day_bars.index[-1])

            slice_15m = df_sym_15m.iloc[:cas_bar_idx + 1]
            slice_daily = df_sym_daily[df_sym_daily.index.date <= eval_date] if df_sym_daily is not None else None

            setup = evaluate_setup_with_params(symbol, slice_15m, slice_daily, min_range_pos, min_vol_ratio, trend_filter)
            if not setup:
                continue

            entry_price = setup["entry"]
            sl = setup["sl"]
            t1 = setup["target1"]
            t2 = setup["target2"]
            direction = setup["direction"]

            next_day_bars = df_sym_15m[df_sym_15m.index.date == next_date]
            if len(next_day_bars) == 0:
                continue

            next_open = float(next_day_bars['Open'].iloc[0])
            gap_pct = (next_open - entry_price) / entry_price * 100.0 if direction == "BUY" else (entry_price - next_open) / entry_price * 100.0

            # First hour morning high / low (09:15 - 10:15)
            morning_bars = next_day_bars[(next_day_bars.index.hour == 9) | ((next_day_bars.index.hour == 10) & (next_day_bars.index.minute <= 15))]
            if len(morning_bars) == 0:
                morning_bars = next_day_bars.iloc[:4]

            m_high = float(morning_bars['High'].max())
            m_low = float(morning_bars['Low'].min())
            m_exit = float(morning_bars['Close'].iloc[-1])

            t1_hit = False
            t2_hit = False
            sl_hit = False

            if direction == "BUY":
                if m_high >= t2:
                    t2_hit = True
                    t1_hit = True
                    pnl_pct = (t2 - entry_price) / entry_price * 100.0
                    exit_reason = "T2_HIT"
                elif m_high >= t1:
                    t1_hit = True
                    # Book 50% at T1, 50% at morning exit (or entry breakeven)
                    rem_exit = max(entry_price, m_exit)
                    pnl_pct = (((t1 - entry_price) * 0.5) + ((rem_exit - entry_price) * 0.5)) / entry_price * 100.0
                    exit_reason = "T1_BOOKED"
                elif m_low <= sl:
                    sl_hit = True
                    pnl_pct = (sl - entry_price) / entry_price * 100.0
                    exit_reason = "SL_HIT"
                else:
                    # Morning square-off
                    pnl_pct = (m_exit - entry_price) / entry_price * 100.0
                    exit_reason = "MORNING_EXIT"
            else: # SELL
                if m_low <= t2:
                    t2_hit = True
                    t1_hit = True
                    pnl_pct = (entry_price - t2) / entry_price * 100.0
                    exit_reason = "T2_HIT"
                elif m_low <= t1:
                    t1_hit = True
                    rem_exit = min(entry_price, m_exit)
                    pnl_pct = (((entry_price - t1) * 0.5) + ((entry_price - rem_exit) * 0.5)) / entry_price * 100.0
                    exit_reason = "T1_BOOKED"
                elif m_high >= sl:
                    sl_hit = True
                    pnl_pct = (entry_price - sl) / entry_price * 100.0
                    exit_reason = "SL_HIT"
                else:
                    pnl_pct = (entry_price - m_exit) / entry_price * 100.0
                    exit_reason = "MORNING_EXIT"

            is_win = (pnl_pct > 0.05) or t1_hit or t2_hit
            is_loss = (pnl_pct < -0.05) and not is_win

            trade_log.append({
                "date": eval_date.strftime("%Y-%m-%d"),
                "ticker": symbol,
                "type": setup["trade_type"],
                "entry": entry_price,
                "t1": t1,
                "gap_pct": round(gap_pct, 2),
                "pnl_pct": round(pnl_pct, 2),
                "exit_reason": exit_reason,
                "is_win": is_win,
                "is_loss": is_loss
            })

    if not trade_log:
        return {"name": config_name, "trades": 0, "win_rate": 0, "profit_factor": 0, "avg_win": 0, "avg_loss": 0, "trades_list": []}

    df = pd.DataFrame(trade_log)
    n_trades = len(df)
    n_wins = len(df[df['is_win'] == True])
    n_losses = len(df[df['is_loss'] == True])
    win_rate = (n_wins / n_trades) * 100.0 if n_trades > 0 else 0.0

    wins_pnl = df[df['is_win'] == True]['pnl_pct']
    losses_pnl = df[df['is_loss'] == True]['pnl_pct']

    avg_win = wins_pnl.mean() if len(wins_pnl) > 0 else 0.0
    avg_loss = losses_pnl.mean() if len(losses_pnl) > 0 else 0.0
    sum_profit = wins_pnl.sum() if len(wins_pnl) > 0 else 0.0
    sum_loss = abs(losses_pnl.sum()) if len(losses_pnl) > 0 else 0.0001
    pf = sum_profit / sum_loss

    favorable_gaps = len(df[df['gap_pct'] > 0])
    gap_rate = (favorable_gaps / n_trades) * 100.0

    return {
        "name": config_name,
        "trades": n_trades,
        "wins": n_wins,
        "losses": n_losses,
        "win_rate": win_rate,
        "gap_rate": gap_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": pf,
        "trades_list": df
    }

def main():
    symbols = [
        "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
        "BHARTIARTL", "LT", "BAJFINANCE", "TATAMOTORS", "MARUTI",
        "SUNPHARMA", "TATASTEEL", "TITAN", "EICHERMOT", "INDIGO",
        "DRREDDY", "ONGC", "SHRIRAMFIN", "TMPV"
    ]
    ticker_ns_list = [s + ".NS" for s in symbols]
    print(f"📥 Downloading historical multi-symbol data (60 Days)...")
    data_15m = yf.download(ticker_ns_list, period="60d", interval="15m", group_by="ticker", auto_adjust=True, progress=False)
    data_daily = yf.download(ticker_ns_list, period="110d", interval="1d", group_by="ticker", auto_adjust=True, progress=False)
    print("✓ Data ready. Comparing configurations...\n")

    configs = [
        ("Config A: Raw Baseline (Range >= 85%, Vol >= 1.35x, No Trend Filter)", 0.85, 1.35, False),
        ("Config B: Selective Institutional (Range >= 90%, Vol >= 1.8x, Daily 20 EMA + RSI Filter)", 0.90, 1.8, True),
        ("Config C: Elite Vyuha Pin (Range >= 93%, Vol >= 2.2x, Daily Trend + Breakout)", 0.93, 2.2, True)
    ]

    results = []
    for name, r_pos, v_ratio, t_filt in configs:
        res = run_simulation(name, r_pos, v_ratio, t_filt, data_15m, data_daily, symbols)
        results.append(res)

    print("=" * 80)
    print("🏆 CAS BTST / STBT ACCURACY & OPTIMIZATION MATRIX")
    print("=" * 80)
    print(f"{'Configuration':<42} {'Trades':<8} {'Win Rate':<10} {'Gap Edge':<10} {'Avg Win':<9} {'Avg Loss':<9} {'Profit Factor'}")
    print("-" * 80)
    for r in results:
        print(f"{r['name'][:40]:<42} {r['trades']:<8} {r['win_rate']:>5.1f}%    {r.get('gap_rate',0):>5.1f}%    {r['avg_win']:>+5.2f}%   {r['avg_loss']:>+5.2f}%   {r['profit_factor']:>6.2f}")
    print("=" * 80)

    best_r = max(results, key=lambda x: x['profit_factor'])
    print(f"\n🌟 BEST PERFORMING SETUP: {best_r['name']}")
    print(f"   Win Rate: {best_r['win_rate']:.1f}% | Profit Factor: {best_r['profit_factor']:.2f} | Favorable Gap: {best_r['gap_rate']:.1f}%\n")

    if len(best_r['trades_list']) > 0:
        print("📋 TOP EXECUTED WINNING TRADES UNDER BEST CONFIGURATION:")
        print("-" * 75)
        df_best = best_r['trades_list']
        for _, t in df_best[df_best['is_win'] == True].head(10).iterrows():
            print(f"[{t['date']}] #{t['ticker']:<10} {t['type']} | Entry: {t['entry']:.2f} | Gap: {t['gap_pct']:>+5.2f}% | PnL: 🟢{t['pnl_pct']:>+5.2f}% ({t['exit_reason']})")
        print("-" * 75)

if __name__ == "__main__":
    main()
