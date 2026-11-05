"""
=============================================================================
🏛️ UMX VYUHA — Institutional CAS (Closing Auction Session) BTST / STBT Engine
=============================================================================
Identifies high-probability institutional Market-On-Close (MOC) flows
between 15:15 IST and 15:25 IST:
1. Closing Range Location (Close >= 85% of day range for BTST, <= 15% for STBT)
2. Cumulative Day VWAP & Benchmark Alignment (LTP > VWAP & PDH for BTST)
3. Closing Auction Session Volume Surge (15:00-15:15 Volume >= 1.5x–2.0x 20-period avg)
4. Institutional Confluence Room (Headroom to Supply 1 / Demand 1 zones)
5. 50% Equilibrium Retracement & High-Res FRVP POC Integration
=============================================================================
"""

import os
import sys
import json
import warnings
warnings.filterwarnings("ignore")
warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=UserWarning)

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

# Force UTF-8 output on Windows
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

from scanner import core, trades, vyuha
import db_storage

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.txt")
CAS_ALERTS_FILE = os.path.join(DATA_DIR, "cas_btst_alerts.json")

alerted_cas_today = set()

def load_cas_alerts():
    global alerted_cas_today
    today_str = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(CAS_ALERTS_FILE):
        try:
            with open(CAS_ALERTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    alerted_cas_today = set(data.get("alerts", []))
                    return
        except Exception:
            pass
    alerted_cas_today = set()

def save_cas_alerts():
    try:
        with open(CAS_ALERTS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "alerts": list(alerted_cas_today)
            }, f, indent=4)
    except Exception:
        pass

def clean_dataframe(df):
    """Flattens MultiIndex columns and ensures numeric OHLCV format."""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    if not isinstance(df.columns, pd.MultiIndex) and 'Close' in df.columns and np.issubdtype(df['Close'].dtype, np.number):
        return df

    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        try:
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        except Exception:
            pass
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['Close'])
    return df

def calculate_atr_15m(df, period=14):
    """Calculates Wilder's ATR on 15m dataframe."""
    df = clean_dataframe(df)
    if len(df) < period + 1:
        return max(0.5, float(df['Close'].iloc[-1]) * 0.01) if len(df) > 0 else 1.0

    high = df['High'].values
    low = df['Low'].values
    close = df['Close'].values

    tr = np.zeros(len(df))
    tr[0] = high[0] - low[0]
    for i in range(1, len(df)):
        hl = high[i] - low[i]
        hpc = abs(high[i] - close[i - 1])
        lpc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hpc, lpc)

    atr = np.zeros(len(df))
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, len(df)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    return float(atr[-1])

def check_cas_setup(ticker, df_15m, df_daily=None):
    """
    Evaluates institutional Closing Auction Session (CAS) BTST or STBT setup.
    Returns setup dictionary or None.
    """
    df_15m = clean_dataframe(df_15m)
    if len(df_15m) < 15:
        return None

    # Identify today's candles (or latest session's candles)
    last_dt = df_15m.index[-1]
    if hasattr(last_dt, 'date'):
        last_date = last_dt.date()
        df_today = df_15m[df_15m.index.date == last_date]
    else:
        df_today = df_15m.iloc[-25:]

    if len(df_today) < 4:
        # Fallback to last 15 bars if timezone mismatch
        df_today = df_15m.iloc[-15:]

    cmp = float(df_today['Close'].iloc[-1])
    day_open = float(df_today['Open'].iloc[0])
    day_high = float(df_today['High'].max())
    day_low = float(df_today['Low'].min())
    day_range = day_high - day_low

    if day_range <= 0 or cmp <= 0:
        return None

    # Range position: 0.0 = at day's low, 1.0 = at day's high
    range_pos = (cmp - day_low) / day_range

    # Intraday Cumulative VWAP
    typ_prices = (df_today['High'] + df_today['Low'] + df_today['Close']) / 3.0
    vols = df_today['Volume']
    total_vol = vols.sum()
    if total_vol > 0:
        day_vwap = float((typ_prices * vols).sum() / total_vol)
    else:
        day_vwap = float(typ_prices.mean())

    # CAS Volume Surge (Latest 15m candle vs 20-period 15m average volume)
    latest_vol = float(df_15m['Volume'].iloc[-1])
    if len(df_15m) >= 21:
        avg_20_vol = float(df_15m['Volume'].iloc[-21:-1].mean())
    else:
        avg_20_vol = float(df_15m['Volume'].mean())
    cas_vol_ratio = latest_vol / max(1.0, avg_20_vol)

    # 15m ATR for adaptive volatility stop
    atr_15m = calculate_atr_15m(df_15m, period=14)

    # MTF Benchmarks & Vyuha Institutional Confluence
    pdh = None
    pdl = None
    vyuha_zones = None
    daily_atr = atr_15m * 2.5

    if df_daily is not None and len(df_daily) >= 5:
        df_daily_clean = clean_dataframe(df_daily)
        if len(df_daily_clean) >= 2:
            pdh = float(df_daily_clean['High'].iloc[-2])
            pdl = float(df_daily_clean['Low'].iloc[-2])
            atr_s = vyuha.calculate_atr(df_daily_clean, period=14)
            daily_atr = float(atr_s.iloc[-1]) if len(atr_s) > 0 and pd.notna(atr_s.iloc[-1]) else (atr_15m * 2.5)
            vyuha_zones = vyuha.calculate_vyuha_zones(df_daily_clean, cmp, daily_atr)

    # High-Resolution 40-Bin FRVP Volume POC
    poc_price, vah, val = vyuha.calculate_frvp_poc(df_today, num_bins=40)
    if poc_price is None:
        poc_price = day_vwap

    today_str = datetime.now().strftime("%Y-%m-%d")

    # =========================================================================
    # 1. BTST SETUP EVALUATION (Buy Today, Sell Tomorrow)
    # =========================================================================
    # Conditions:
    # 1. Closing in top 15% of day range (range_pos >= 0.85, ideal >= 0.88)
    # 2. Bullish session (cmp > day_open)
    # 3. Holding strictly above Day VWAP (cmp > day_vwap)
    # 4. Holding above or breaking Previous Day High (cmp >= pdh * 0.995 if pdh exists)
    # 5. CAS Volume expansion (cas_vol_ratio >= 1.4, ideal >= 2.0x)
    # 6. Above FRVP Volume POC
    # 7. Ample clearance to overhead Supply 1 zone (> 1.2%)
    # =========================================================================
    is_btst = False
    if range_pos >= 0.85 and cmp > day_vwap and cmp > day_open:
        pdh_ok = (pdh is None) or (cmp >= pdh * 0.995)
        poc_ok = (poc_price is None) or (cmp >= poc_price * 0.997)
        vol_ok = cas_vol_ratio >= 1.35

        # Check supply zone headroom
        supply_headroom_ok = True
        if vyuha_zones and vyuha_zones.get("supply_1"):
            sup1_bot = vyuha_zones["supply_1"]["bot"]
            if sup1_bot > cmp:
                headroom_pct = (sup1_bot - cmp) / cmp * 100
                if headroom_pct < 1.2:
                    supply_headroom_ok = False

        if pdh_ok and poc_ok and vol_ok and supply_headroom_ok:
            is_btst = True

    if is_btst:
        # Tight generic SL (1.5x ATR / Max 2.0% risk)
        raw_sl = max(day_vwap * 0.996, cmp - (1.5 * atr_15m))
        sl = max(raw_sl, cmp * 0.982)  # Cap SL at 1.8% max risk
        risk = cmp - sl
        if risk <= 0:
            sl = cmp * 0.985
            risk = cmp - sl

        # Targets: T1 = 1.5R (approx +1.8% to +2.5% morning gap-up), T2 = 2.5R - 3.0R
        target1 = round(cmp + max(1.5 * risk, cmp * 0.018), 2)
        target2 = round(cmp + max(2.8 * risk, cmp * 0.035), 2)
        sl = round(sl, 2)
        entry = round(cmp, 2)

        score = float(range_pos * 100.0) + (cas_vol_ratio * 15.0)
        reason_desc = (f"CAS Inflow ({cas_vol_ratio:.1f}x Vol) | Cls @ {range_pos*100:.0f}% Range | "
                       f"Above VWAP & {'PDH' if pdh and cmp >= pdh else 'EQ Floor'}")

        return {
            "ticker": ticker,
            "direction": "BUY",
            "trade_type": "BTST",
            "strategy": "CAS_BTST",
            "entry": entry,
            "sl": sl,
            "target1": target1,
            "target2": target2,
            "range_pos": range_pos,
            "cas_vol_ratio": cas_vol_ratio,
            "day_vwap": round(day_vwap, 2),
            "poc": round(poc_price, 2),
            "score": score,
            "reason": reason_desc,
            "setup_date": today_str
        }

    # =========================================================================
    # 2. STBT SETUP EVALUATION (Sell Today, Buy Tomorrow)
    # =========================================================================
    # Conditions:
    # 1. Closing in bottom 15% of day range (range_pos <= 0.15, ideal <= 0.12)
    # 2. Bearish session (cmp < day_open)
    # 3. Trapped strictly below Day VWAP (cmp < day_vwap)
    # 4. Breaking or below Previous Day Low (cmp <= pdl * 1.005 if pdl exists)
    # 5. CAS Volume expansion on breakdown (cas_vol_ratio >= 1.35)
    # 6. Below FRVP Volume POC
    # 7. Ample clearance down to Demand 1 zone (> 1.2%)
    # =========================================================================
    is_stbt = False
    if range_pos <= 0.15 and cmp < day_vwap and cmp < day_open:
        pdl_ok = (pdl is None) or (cmp <= pdl * 1.005)
        poc_ok = (poc_price is None) or (cmp <= poc_price * 1.003)
        vol_ok = cas_vol_ratio >= 1.35

        # Check demand zone headroom
        demand_headroom_ok = True
        if vyuha_zones and vyuha_zones.get("demand_1"):
            dem1_top = vyuha_zones["demand_1"]["top"]
            if dem1_top < cmp:
                headroom_pct = (cmp - dem1_top) / cmp * 100
                if headroom_pct < 1.2:
                    demand_headroom_ok = False

        if pdl_ok and poc_ok and vol_ok and demand_headroom_ok:
            is_stbt = True

    if is_stbt:
        raw_sl = min(day_vwap * 1.004, cmp + (1.5 * atr_15m))
        sl = min(raw_sl, cmp * 1.018)  # Cap SL at 1.8% max risk
        risk = sl - cmp
        if risk <= 0:
            sl = cmp * 1.015
            risk = sl - cmp

        target1 = round(cmp - max(1.5 * risk, cmp * 0.018), 2)
        target2 = round(cmp - max(2.8 * risk, cmp * 0.035), 2)
        sl = round(sl, 2)
        entry = round(cmp, 2)

        score = float((1.0 - range_pos) * 100.0) + (cas_vol_ratio * 15.0)
        reason_desc = (f"CAS Liquidation ({cas_vol_ratio:.1f}x Vol) | Cls @ {range_pos*100:.0f}% Range | "
                       f"Below VWAP & {'PDL' if pdl and cmp <= pdl else 'POC Floor'}")

        return {
            "ticker": ticker,
            "direction": "SELL",
            "trade_type": "STBT",
            "strategy": "CAS_STBT",
            "entry": entry,
            "sl": sl,
            "target1": target1,
            "target2": target2,
            "range_pos": range_pos,
            "cas_vol_ratio": cas_vol_ratio,
            "day_vwap": round(day_vwap, 2),
            "poc": round(poc_price, 2),
            "score": score,
            "reason": reason_desc,
            "setup_date": today_str
        }

    return None

def run_cas_scan(send_telegram_fn=None, force=False):
    """
    Executes the institutional CAS (Closing Auction Session) BTST / STBT Scan.
    Runs between 15:15 IST and 15:25 IST.
    """
    load_cas_alerts()

    tickers = core.get_dynamic_tickers()
    if not tickers:
        print("[WARN] Ticker universe is empty for CAS BTST/STBT scan.")
        return []

    print(f"\n===============================================================")
    print(f"🏛️ [INFO] Running Institutional CAS BTST / STBT Scan @ {datetime.now().strftime('%H:%M:%S')} IST")
    print(f"   Universe: {len(tickers)} Liquid F&O Symbols | Timeframe: 15m + Daily")
    print(f"===============================================================")

    ticker_ns_list = [t + ".NS" for t in tickers]

    # Download 15m intraday data and daily data
    try:
        df_15m_all = yf.download(
            ticker_ns_list,
            period="5d",
            interval="15m",
            group_by="ticker",
            progress=False,
            auto_adjust=True
        )
    except Exception as e:
        print(f"[ERROR] Failed downloading 15m batch data for CAS scan: {e}")
        return []

    try:
        df_daily_all = yf.download(
            ticker_ns_list,
            period="1mo",
            interval="1d",
            group_by="ticker",
            progress=False,
            auto_adjust=True
        )
    except Exception as e:
        df_daily_all = None

    candidates_btst = []
    candidates_stbt = []

    for ticker in tickers:
        ticker_ns = ticker + ".NS"
        try:
            # Extract 15m dataframe for ticker
            if len(tickers) == 1:
                df_ticker_15m = df_15m_all
                df_ticker_daily = df_daily_all
            elif ticker_ns in df_15m_all.columns.levels[0]:
                df_ticker_15m = df_15m_all[ticker_ns].dropna()
                df_ticker_daily = df_daily_all[ticker_ns].dropna() if df_daily_all is not None and ticker_ns in df_daily_all.columns.levels[0] else None
            else:
                continue

            if len(df_ticker_15m) < 15:
                continue

            res = check_cas_setup(ticker, df_ticker_15m, df_ticker_daily)
            if res:
                if res["trade_type"] == "BTST":
                    candidates_btst.append(res)
                elif res["trade_type"] == "STBT":
                    candidates_stbt.append(res)
        except Exception as e:
            pass

    # Sort and pick top 2 BTST and top 2 STBT
    candidates_btst.sort(key=lambda x: x["score"], reverse=True)
    candidates_stbt.sort(key=lambda x: x["score"], reverse=True)

    selected_setups = candidates_btst[:2] + candidates_stbt[:2]

    pt = trades.load_paper_trades()
    active_tickers = set([t["ticker"] for t in pt if t["status"] in ["ACTIVE", "PARTIAL_EXIT", "PENDING"]])

    new_alerts = []
    for c in selected_setups:
        ticker = c["ticker"]
        alert_key = f"{ticker}_{c['trade_type']}_{c['setup_date']}"

        if alert_key not in alerted_cas_today:
            new_alerts.append(c)
            alerted_cas_today.add(alert_key)

            # Auto initiate paper trade if not already active
            if ticker not in active_tickers:
                trades.initiate_paper_trade(ticker, {
                    "entry": c["entry"],
                    "sl": c["sl"],
                    "target1": c["target1"],
                    "target2": c["target2"],
                    "direction": c["direction"],
                    "setup_date": c["setup_date"],
                    "trade_type": c["trade_type"],
                    "strategy": c["strategy"]
                })

            # Record into today's daily recommendations
            try:
                db_storage.record_daily_recommendation(
                    ticker=ticker,
                    strategy=c["strategy"],
                    direction=c["direction"],
                    trade_type=c["trade_type"],
                    entry=c["entry"],
                    sl=c["sl"],
                    target1=c["target1"],
                    target2=c["target2"],
                    reason=c["reason"]
                )
            except Exception as e:
                print(f"[WARN] Error saving CAS recommendation to DB: {e}")

            # Append to Watchlist
            dir_label = "Long" if c["direction"] == "BUY" else "Short"
            new_line = f"{ticker} | {c['strategy']} | {dir_label} | {c['trade_type']} | {c['entry']:.2f} | PROJ_UP_2.5 | {c['target1']:.2f} | {c['target2']:.2f}\n"
            try:
                db_storage.append_to_watchlist(new_line.strip())
            except Exception:
                pass

            lines = []
            updated = False
            if os.path.exists(WATCHLIST_FILE):
                with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            if line.split('|')[0].strip() == ticker:
                                lines.append(new_line)
                                updated = True
                            else:
                                lines.append(line)
            if not updated:
                lines.append(new_line)
            with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines)

    save_cas_alerts()

    # Dispatch dedicated Telegram alert if any new setups identified
    if new_alerts and send_telegram_fn:
        lines_out = [
            f"🏛️ *VYUHA CAS BTST / STBT SIGNALS* ({len(new_alerts)} Setups)\n"
            f"*(Closing Auction Session Institutional MOC Inflow)*\n"
        ]
        for c in new_alerts:
            action_icon = "🟢" if c["direction"] == "BUY" else "🔴"
            action_text = f"{c['trade_type']} {c['direction']}"
            lines_out.append(
                f"• {action_icon} *{action_text}: #{c['ticker']}*\n"
                f"  Entry: `{c['entry']:.2f}` | SL: `{c['sl']:.2f}` | T1: `{c['target1']:.2f}` | T2: `{c['target2']:.2f}`\n"
                f"  {c['reason']}\n"
                f"  ⏱️ *Hold:* Overnight (Target Exit: 09:18–09:45 AM next morning)\n"
            )
        lines_out.append("⚠️ Not SEBI registered. Strictly educational. Mandatory generic SL (1.5x ATR / max 2% risk).")
        full_msg = "\n".join(lines_out)
        print(full_msg)
        try:
            send_telegram_fn(full_msg)
        except Exception as e:
            print(f"[WARN] Error dispatching Telegram alert: {e}")

    print(f"🏁 [INFO] CAS BTST / STBT Scan complete: {len(new_alerts)} new candidates alerted.")
    return new_alerts
