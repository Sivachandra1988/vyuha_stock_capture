"""
=============================================================================
🏛️ UMX VYUHA — Comprehensive CAS Strategy Validation Engine
=============================================================================
Deep quantitative analysis of Closing Auction Session (CAS) mechanics:
1. Impact of CAS Volume Expansion (1.0x vs 1.8x vs 2.5x)
2. Impact of Closing Range Location (80% vs 88% vs 95%)
3. Impact of Stop Loss Width (1.2% tight vs 2.2% swing vs Day Low)
4. Full Day Holding vs 09:30 AM Quick Gap Exit
=============================================================================
"""

import os
import sys
import warnings
warnings.filterwarnings("ignore")
warnings.simplefilter(action='ignore', category=FutureWarning)

import numpy as np
import pandas as pd
import yfinance as yf

# Force UTF-8 output
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def fetch_universe_data(symbols, days=60):
    ticker_ns_list = [s + ".NS" for s in symbols]
    print(f"📥 Fetching 60d 15m intraday + daily data for {len(symbols)} tickers...")
    df_15m = yf.download(ticker_ns_list, period=f"{days}d", interval="15m", group_by="ticker", auto_adjust=True, progress=False)
    df_daily = yf.download(ticker_ns_list, period=f"{days+40}d", interval="1d", group_by="ticker", auto_adjust=True, progress=False)
    return df_15m, df_daily

def analyze_cas_accuracy(symbols, df_15m_all, df_daily_all, min_range=0.88, min_vol=1.8, sl_pct=0.022, exit_strategy="MORNING_TARGET"):
    """
    Simulates CAS BTST trades:
    exit_strategy:
      - 'QUICK_OPEN': Exit immediately at 09:15-09:20 Open
      - 'MORNING_TARGET': Target 1 (+1.8%), SL (sl_pct), trail to BE, cutoff at 11:30 AM
      - 'FULL_DAY': Hold full next trading day till T2 or EOD close
    """
    trades = []

    for sym in symbols:
        ticker_ns = sym + ".NS"
        if ticker_ns not in df_15m_all.columns.levels[0]:
            continue

        df_15m = df_15m_all[ticker_ns].dropna()
        df_d = df_daily_all[ticker_ns].dropna() if ticker_ns in df_daily_all.columns.levels[0] else None

        if len(df_15m) < 40:
            continue

        try:
            if df_15m.index.tz is None:
                df_15m.index = df_15m.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
            else:
                df_15m.index = df_15m.index.tz_convert("Asia/Kolkata")
        except Exception:
            pass

        dates = sorted(list(set(df_15m.index.date)))
        if len(dates) < 3:
            continue

        for i in range(len(dates) - 1):
            cur_date = dates[i]
            nxt_date = dates[i + 1]

            day_bars = df_15m[df_15m.index.date == cur_date]
            if len(day_bars) < 8:
                continue

            # Identify 15:00 / 15:15 CAS bars
            cas_bars = day_bars[(day_bars.index.hour == 15) & (day_bars.index.minute >= 0)]
            if len(cas_bars) == 0:
                continue

            cas_bar = cas_bars.iloc[-1]
            entry_price = float(cas_bar['Close'])

            day_high = float(day_bars['High'].max())
            day_low = float(day_bars['Low'].min())
            day_open = float(day_bars['Open'].iloc[0])
            day_range = day_high - day_low

            if day_range <= 0 or entry_price <= 0:
                continue

            range_pos = (entry_price - day_low) / day_range

            # VWAP
            typ_price = (day_bars['High'] + day_bars['Low'] + day_bars['Close']) / 3.0
            day_vwap = float((typ_price * day_bars['Volume']).sum() / max(1, day_bars['Volume'].sum()))

            # CAS Volume Ratio
            cas_vol = float(cas_bar['Volume'])
            avg_vol = float(day_bars['Volume'].iloc[:-1].mean()) if len(day_bars) > 1 else cas_vol
            vol_ratio = cas_vol / max(1.0, avg_vol)

            # Daily Trend Context
            daily_uptrend = True
            if df_d is not None and len(df_d) >= 20:
                sub_d = df_d[df_d.index.date <= cur_date]
                if len(sub_d) >= 20:
                    d_ema20 = float(sub_d['Close'].ewm(span=20, adjust=False).mean().iloc[-1])
                    pdh = float(sub_d['High'].iloc[-2]) if len(sub_d) >= 2 else entry_price
                    daily_uptrend = (entry_price >= d_ema20 * 0.99) and (entry_price >= pdh * 0.99)

            # Check BTST Criteria
            if range_pos >= min_range and vol_ratio >= min_vol and entry_price > day_vwap and entry_price > day_open and daily_uptrend:
                # Next Day Execution
                nxt_bars = df_15m[df_15m.index.date == nxt_date]
                if len(nxt_bars) == 0:
                    continue

                nxt_open = float(nxt_bars['Open'].iloc[0])
                gap_pct = (nxt_open - entry_price) / entry_price * 100.0

                # Define SL and Targets
                sl_price = round(min(day_low * 0.997, entry_price * (1.0 - sl_pct)), 2)
                t1_price = round(entry_price * 1.018, 2) # +1.8% target
                t2_price = round(entry_price * 1.035, 2) # +3.5% target

                # Simulation based on exit_strategy
                if exit_strategy == "QUICK_OPEN":
                    pnl_pct = gap_pct
                    is_win = pnl_pct > 0.05
                    exit_reason = "OPEN_GAP"
                elif exit_strategy == "MORNING_TARGET":
                    # Evaluate 09:15 - 11:15
                    m_bars = nxt_bars[(nxt_bars.index.hour == 9) | (nxt_bars.index.hour == 10) | ((nxt_bars.index.hour == 11) & (nxt_bars.index.minute <= 15))]
                    m_high = float(m_bars['High'].max()) if len(m_bars) > 0 else nxt_open
                    m_low = float(m_bars['Low'].min()) if len(m_bars) > 0 else nxt_open
                    m_exit = float(m_bars['Close'].iloc[-1]) if len(m_bars) > 0 else nxt_open

                    if m_high >= t2_price:
                        pnl_pct = (t2_price - entry_price) / entry_price * 100.0
                        is_win = True
                        exit_reason = "T2_HIT"
                    elif m_high >= t1_price:
                        # 50% booked at T1, remaining 50% at BE or morning exit
                        rem = max(entry_price, m_exit)
                        pnl_pct = (((t1_price - entry_price) * 0.5) + ((rem - entry_price) * 0.5)) / entry_price * 100.0
                        is_win = True
                        exit_reason = "T1_HIT"
                    elif m_low <= sl_price:
                        pnl_pct = (sl_price - entry_price) / entry_price * 100.0
                        is_win = False
                        exit_reason = "SL_HIT"
                    else:
                        pnl_pct = (m_exit - entry_price) / entry_price * 100.0
                        is_win = pnl_pct > 0.05
                        exit_reason = "MORNING_SQUAREOFF"
                else: # FULL_DAY
                    d_high = float(nxt_bars['High'].max())
                    d_low = float(nxt_bars['Low'].min())
                    d_close = float(nxt_bars['Close'].iloc[-1])

                    if d_high >= t2_price:
                        pnl_pct = (t2_price - entry_price) / entry_price * 100.0
                        is_win = True
                        exit_reason = "T2_HIT"
                    elif d_high >= t1_price:
                        pnl_pct = (((t1_price - entry_price) * 0.5) + ((max(entry_price, d_close) - entry_price) * 0.5)) / entry_price * 100.0
                        is_win = True
                        exit_reason = "T1_HIT"
                    elif d_low <= sl_price:
                        pnl_pct = (sl_price - entry_price) / entry_price * 100.0
                        is_win = False
                        exit_reason = "SL_HIT"
                    else:
                        pnl_pct = (d_close - entry_price) / entry_price * 100.0
                        is_win = pnl_pct > 0.05
                        exit_reason = "EOD_CLOSE"

                trades.append({
                    "date": cur_date.strftime("%Y-%m-%d"),
                    "ticker": sym,
                    "entry": entry_price,
                    "t1": t1_price,
                    "sl": sl_price,
                    "gap_pct": round(gap_pct, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "is_win": is_win,
                    "exit_reason": exit_reason,
                    "range_pos": round(range_pos, 2),
                    "vol_ratio": round(vol_ratio, 2)
                })

    return trades

def main():
    symbols = [
        "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "RELIANCE",
        "BHARTIARTL", "LT", "BAJFINANCE", "MARUTI", "SUNPHARMA",
        "TATASTEEL", "TITAN", "EICHERMOT", "INDIGO", "DRREDDY",
        "ONGC", "SHRIRAMFIN", "TMPV"
    ]
    df_15m, df_daily = fetch_universe_data(symbols, days=60)

    print("=" * 80)
    print("🔬 CAS STRATEGY ACCURACY UNDER DIFFERENT TRADING MODES")
    print("=" * 80)

    scenarios = [
        ("Mode 1: High Selectivity (Range >= 90%, Vol >= 2.0x, Morning Target Exit)", 0.90, 2.0, 0.022, "MORNING_TARGET"),
        ("Mode 2: Elite Pin Setup (Range >= 92%, Vol >= 2.5x, Morning Target Exit)", 0.92, 2.5, 0.022, "MORNING_TARGET"),
        ("Mode 3: Pure Overnight Gap Scalp (Exit @ 09:15 Open, Range >= 90%, Vol >= 2.0x)", 0.90, 2.0, 0.022, "QUICK_OPEN"),
        ("Mode 4: Full Day Holding (Hold until 15:15 Next Day or T2, Range >= 90%, Vol >= 2.0x)", 0.90, 2.0, 0.025, "FULL_DAY"),
        ("Mode 5: Baseline Loose Filter (Range >= 85%, Vol >= 1.5x, Morning Target Exit)", 0.85, 1.5, 0.022, "MORNING_TARGET")
    ]

    for label, r_min, v_min, sl_val, strat in scenarios:
        res = analyze_cas_accuracy(symbols, df_15m, df_daily, min_range=r_min, min_vol=v_min, sl_pct=sl_val, exit_strategy=strat)
        df_r = pd.DataFrame(res)
        if len(df_r) == 0:
            print(f"{label}: 0 trades triggered.")
            continue

        n_trades = len(df_r)
        wins = df_r[df_r['is_win'] == True]
        losses = df_r[df_r['is_win'] == False]
        win_rate = (len(wins) / n_trades) * 100.0

        avg_win = wins['pnl_pct'].mean() if len(wins) > 0 else 0.0
        avg_loss = losses['pnl_pct'].mean() if len(losses) > 0 else 0.0
        sum_win = wins['pnl_pct'].sum() if len(wins) > 0 else 0.0
        sum_loss = abs(losses['pnl_pct'].sum()) if len(losses) > 0 else 0.0001
        pf = sum_win / sum_loss

        gaps = df_r[df_r['gap_pct'] > 0]
        gap_win_rate = (len(gaps) / n_trades) * 100.0
        avg_gap = df_r['gap_pct'].mean()

        print(f"\n📌 {label}")
        print(f"   • Total Trades:     {n_trades} setups (approx {n_trades/40:.1f} per day)")
        print(f"   • Win Rate:         {win_rate:.1f}%")
        print(f"   • Morning Gap Edge: {gap_win_rate:.1f}% positive opens (Avg Gap: {avg_gap:+.2f}%)")
        print(f"   • Avg Win / Loss:   +{avg_win:.2f}% / {avg_loss:.2f}%")
        print(f"   • Profit Factor:    {pf:.2f}")

    # Show recent trades from best mode
    best_trades = analyze_cas_accuracy(symbols, df_15m, df_daily, min_range=0.90, min_vol=2.0, sl_pct=0.022, exit_strategy="MORNING_TARGET")
    df_best = pd.DataFrame(best_trades)
    print("\n" + "=" * 80)
    print("📋 RECENT TRADES BREAKDOWN (High Selectivity Mode):")
    print("=" * 80)
    print(f"{'Date':<11} {'Ticker':<11} {'Entry':<9} {'T1':<9} {'Gap%':<8} {'PnL%':<8} {'Exit Reason'}")
    print("-" * 75)
    for _, t in df_best.tail(15).iterrows():
        icon = "🟢" if t['is_win'] else "🔴"
        print(f"{t['date']:<11} #{t['ticker']:<10} {t['entry']:<9.2f} {t['t1']:<9.2f} {t['gap_pct']:>+5.2f}%  {icon}{t['pnl_pct']:>+5.2f}%  {t['exit_reason']}")
    print("-" * 75)

if __name__ == "__main__":
    main()
