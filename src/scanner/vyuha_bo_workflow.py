"""
=============================================================================
🏛️ VYUHA-BO INSTITUTIONAL WORKFLOW ENGINE
Dual-Layer Confluence Architecture:
Layer 1: UMX Vyuha Institutional Structure Mapping (Demand 1/2, Supply 1/2, FRVP POC, Equilibrium Zone [VAL, VAH])
Layer 2: BO (Breakout / Breakdown) Directional Validation Gate
=============================================================================
Filters out false signals (e.g., buying under overhead Supply 1 or into an
Equilibrium breakdown like MAXHEALTH), and only issues high-conviction
recommendations where price is confirmed breaking UP or DOWN out of key zones.
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

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.join(BASE_DIR, "src")
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from scanner import vyuha, trades, core, options
import db_storage

WORKFLOW_ALERTS_FILE = os.path.join(DATA_DIR, "vyuha_bo_alerts.json")
alerted_workflow_today = set()

def load_workflow_alerts():
    global alerted_workflow_today
    today_str = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(WORKFLOW_ALERTS_FILE):
        try:
            with open(WORKFLOW_ALERTS_FILE, "r") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    alerted_workflow_today = set(data.get("alerts", []))
                    return
        except Exception:
            pass
    alerted_workflow_today = set()

def save_workflow_alerts():
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(WORKFLOW_ALERTS_FILE, "w") as f:
            json.dump({
                "date": today_str,
                "alerts": list(alerted_workflow_today)
            }, f, indent=4)
    except Exception:
        pass

def clean_dataframe(df):
    """Safely flattens MultiIndex columns and normalizes OHLCV DataFrame."""
    return vyuha.clean_dataframe(df)

def calculate_vwap(df):
    """Calculates session VWAP from intraday bars."""
    df = df.copy()
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3.0
    tp_vol = typical_price * df['Volume']
    dates = df.index.date
    cum_tp_vol = tp_vol.groupby(dates).cumsum()
    cum_vol = df['Volume'].groupby(dates).cumsum()
    cum_vol = cum_vol.replace(0, np.nan)
    vwap = cum_tp_vol / cum_vol
    return vwap.bfill().ffill()

def validate_vyuha_bo_setup(ticker, df_intraday, df_daily, candidate_direction=None):
    """
    Validates a trade candidate against Vyuha MTF Institutional Zones + BO Gate.
    
    Returns a dict with:
        is_valid: bool
        direction: 'BUY' | 'SELL' | None
        setup_type: str
        bo_status: str
        entry: float
        sl: float
        target1: float
        target2: float
        risk_pts: float
        rejection_reason: str (if invalid)
        metadata: dict
    """
    df_intraday = clean_dataframe(df_intraday)
    df_daily = clean_dataframe(df_daily)

    if len(df_intraday) < 30 or len(df_daily) < 15:
        return {
            "is_valid": False,
            "ticker": ticker,
            "rejection_reason": f"Insufficient bars (intraday: {len(df_intraday)}, daily: {len(df_daily)})"
        }

    # 1. Base price & ATR
    c_close = float(df_intraday['Close'].iloc[-1])
    c_open = float(df_intraday['Open'].iloc[-1])
    c_high = float(df_intraday['High'].iloc[-1])
    c_low = float(df_intraday['Low'].iloc[-1])

    # Check same-day stop-loss cooldown (Prevent revenge trading / whipsaw flips)
    if db_storage.is_ticker_in_cooldown_today(ticker):
        return {
            "is_valid": False,
            "ticker": ticker,
            "rejection_reason": f"SAME-DAY COOLDOWN: #{ticker} was stopped out today. Lockout active to prevent revenge trading/whipsaws."
        }
    
    atr_daily_series = vyuha.calculate_atr(df_daily, 14)
    atr_daily = float(atr_daily_series.iloc[-1]) if len(atr_daily_series) > 0 else (c_close * 0.015)
    
    atr_intra_series = vyuha.calculate_atr(df_intraday, 14)
    atr_intra = float(atr_intra_series.iloc[-1]) if len(atr_intra_series) > 0 else (c_close * 0.005)

    # Higher Timeframe (Daily) Trend & Regime
    daily_regime = "NEUTRAL"
    if len(df_daily) >= 20:
        d_ema10 = float(df_daily['Close'].ewm(span=10, adjust=False).mean().iloc[-1])
        d_ema20 = float(df_daily['Close'].ewm(span=20, adjust=False).mean().iloc[-1])
        delta = df_daily['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        d_rsi = float((100 - (100 / (1 + rs))).iloc[-1])
        if d_ema10 > d_ema20 and d_rsi >= 50:
            daily_regime = "BULLISH"
        elif d_ema10 < d_ema20 and d_rsi <= 50:
            daily_regime = "BEARISH"

    # 2. Vyuha Confluence Zones
    zones = vyuha.calculate_vyuha_zones(df_daily, c_close, atr_daily)
    if not zones:
        return {
            "is_valid": False,
            "ticker": ticker,
            "rejection_reason": "Failed to resolve Vyuha confluence zones"
        }

    d1 = zones['demand_1']
    d2_val = zones['demand_2'] if isinstance(zones['demand_2'], (int, float)) else zones['demand_2'].get('center', 0.0)
    s1 = zones['supply_1']
    s2_val = zones['supply_2'] if isinstance(zones['supply_2'], (int, float)) else zones['supply_2'].get('center', 0.0)
    pdh = zones['pdh']
    pdl = zones['pdl']

    # 3. FRVP POC & 70% Value Area (Equilibrium Zone [VAL, VAH])
    lookback_bars = min(len(df_intraday), 40)
    poc_price, vah, val = vyuha.calculate_frvp_poc(df_intraday.tail(lookback_bars))
    if poc_price is None or poc_price <= 0:
        poc_price = c_close
    if vah is None or vah <= 0:
        vah = c_close + (0.5 * atr_intra)
    if val is None or val <= 0:
        val = c_close - (0.5 * atr_intra)

    eq_mid = (vah + val) / 2.0

    # 3b. Value Area Width Quality Gate & Volume Expansion
    va_width_pct = ((vah - val) / val) * 100.0 if val > 0 else 0.0
    vol_sma20 = float(df_intraday['Volume'].rolling(20).mean().iloc[-1]) if 'Volume' in df_intraday.columns and len(df_intraday) >= 20 else 1.0
    c_vol = float(df_intraday['Volume'].iloc[-1]) if 'Volume' in df_intraday.columns else 1.0
    vol_mult = (c_vol / vol_sma20) if vol_sma20 > 0 else 1.0

    # 4. Moving Averages & Session VWAP
    df_intraday = df_intraday.copy()
    df_intraday['EMA_9'] = vyuha.calculate_ema(df_intraday['Close'], 9)
    df_intraday['EMA_21'] = vyuha.calculate_ema(df_intraday['Close'], 21)
    df_intraday['EMA_200'] = vyuha.calculate_ema(df_intraday['Close'], 200)
    df_intraday['VWAP'] = calculate_vwap(df_intraday)

    latest = df_intraday.iloc[-1]
    ema9 = float(latest['EMA_9'])
    ema21 = float(latest['EMA_21'])
    ema200 = float(latest['EMA_200']) if not np.isnan(latest['EMA_200']) else (c_close * 0.98)
    vwap_val = float(latest['VWAP'])

    is_bull_ribbon = (ema9 >= ema21) and (c_close >= ema9)
    is_bear_ribbon = (ema9 <= ema21) and (c_close <= ema9)

    # 5. AVWAP & Trap detection
    avwap_val, avwap_slope, is_green_diamond = vyuha.calculate_zone_avwap(df_intraday.tail(20))
    is_bull_trap, is_bear_trap, lower_wick_ratio, upper_wick_ratio = vyuha.evaluate_anti_trap_gates(latest)

    # 6. Breakout / Breakdown (BO / BD) Evaluation
    # Distance to Supply 1 and Demand 1
    dist_to_s1_bot = ((s1['bot'] - c_close) / c_close) * 100.0 if s1['bot'] > c_close else 0.0
    dist_to_d1_top = ((c_close - d1['top']) / c_close) * 100.0 if c_close > d1['top'] else 0.0

    # Check for Traps first:
    # A) Buying while trapped below Supply 1 or in Equilibrium Breakdown
    if candidate_direction in ["BUY", "LONG", "B/O Call", "Long"]:
        # Block if price is directly under Supply 1 without breaking it (< 0.6% below)
        if s1['bot'] > c_close and dist_to_s1_bot < 0.60:
            return {
                "is_valid": False,
                "ticker": ticker,
                "rejection_reason": f"BUY BLOCKED: Price ({c_close:.2f}) is trapped directly under Supply 1 [{s1['bot']:.2f} - {s1['top']:.2f}] (Distance: {dist_to_s1_bot:.2f}%)"
            }
        # Block if price is below POC and below VWAP in a Bear Ribbon (the MAXHEALTH scenario)
        if (c_close < poc_price) and (c_close < vwap_val) and is_bear_ribbon:
            return {
                "is_valid": False,
                "ticker": ticker,
                "rejection_reason": f"BUY BLOCKED: Bear Trend breakdown below POC ({poc_price:.2f}) and VWAP ({vwap_val:.2f}) into Equilibrium floor ({val:.2f})"
            }

    # B) Selling while trapped directly above Demand 1 or in Equilibrium Breakout
    if candidate_direction in ["SELL", "SHORT", "Short"]:
        if c_close > d1['top'] and dist_to_d1_top < 0.60:
            return {
                "is_valid": False,
                "ticker": ticker,
                "rejection_reason": f"SELL BLOCKED: Price ({c_close:.2f}) is sitting directly on Demand 1 [{d1['bot']:.2f} - {d1['top']:.2f}] (Distance: {dist_to_d1_top:.2f}%)"
            }
        if (c_close > poc_price) and (c_close > vwap_val) and is_bull_ribbon:
            return {
                "is_valid": False,
                "ticker": ticker,
                "rejection_reason": f"SELL BLOCKED: Bullish momentum above POC ({poc_price:.2f}) and VWAP ({vwap_val:.2f})"
            }

    # 7. Identify Valid BO / BD Triggers
    detected_direction = None
    setup_type = None
    bo_status = None
    sl = None
    target1 = None
    target2 = None

    # --- 🟢 BULLISH BREAKOUT / EXPANSION (BO-UP) ---
    # Trigger 1: Supply 1 Cleared -> Expansion towards Supply 2
    if c_close >= s1['top'] and c_close >= poc_price and not is_bull_trap:
        detected_direction = "BUY"
        setup_type = "SUPPLY_1_EXPANSION_BREAKOUT"
        bo_status = f"🚀 SUPPLY BROKE → Tgt 2: {s2_val:.1f}"
        sl = max(s1['bot'] - (0.3 * atr_intra), c_close - (1.5 * atr_intra))
        target1 = c_close + 1.8 * (c_close - sl)
        target2 = max(s2_val, c_close + 2.5 * (c_close - sl))

    # Trigger 2: Equilibrium Zone Breakout (Cleared VAH + POC + VWAP with Bull Ribbon)
    elif c_close >= vah and c_close >= poc_price and c_close >= vwap_val and is_bull_ribbon and not is_bull_trap:
        # Quality Gate: Suppress Long breakouts if Daily timeframe is Bearish
        if daily_regime == "BEARISH":
            return {
                "is_valid": False,
                "ticker": ticker,
                "rejection_reason": f"BUY BLOCKED: #{ticker} Daily timeframe is BEARISH. Long breakout suppressed."
            }
        # Quality Gate: Suppress breakouts in narrow chop zones without volume expansion
        if va_width_pct < 0.90 and vol_mult < 1.25:
            return {
                "is_valid": False,
                "ticker": ticker,
                "rejection_reason": f"BUY BLOCKED: Narrow Equilibrium Box ({va_width_pct:.2f}% < 0.90%) without expansion volume ({vol_mult:.2f}x < 1.25x)."
            }
        detected_direction = "BUY"
        setup_type = "EQUILIBRIUM_VAH_BREAKOUT"
        bo_status = f"🟢 EQUILIBRIUM BREAKOUT → Supply 1: {s1['bot']:.1f}"
        sl = min(val, c_close - (1.2 * atr_intra))
        target1 = c_close + 1.8 * (c_close - sl)
        target2 = s1['center'] if s1['center'] > target1 else (c_close + 2.5 * (c_close - sl))

    # Trigger 3: Demand 1 Bounce Breakout (Rejection of Demand floor + reclaim of POC / EMA9)
    elif (c_low <= d1['top'] and c_close >= d1['bot']) and lower_wick_ratio >= 0.28 and c_close >= c_open:
        detected_direction = "BUY"
        setup_type = "DEMAND_1_BOUNCE_BREAKOUT"
        bo_status = f"🛡️ DEMAND BOUNCE → POC/Supply: {s1['bot']:.1f}"
        sl = min(c_low, d1['bot']) - (0.3 * atr_intra)
        target1 = c_close + 1.8 * (c_close - sl)
        target2 = s1['bot'] if s1['bot'] > target1 else (c_close + 2.5 * (c_close - sl))

    # --- 🔴 BEARISH BREAKDOWN / REJECTION (BD-DOWN) ---
    # Trigger 1: Demand 1 Broken -> Expansion towards Demand 2
    elif c_close <= d1['bot'] and c_close <= poc_price and not is_bear_trap:
        detected_direction = "SELL"
        setup_type = "DEMAND_1_EXPANSION_BREAKDOWN"
        bo_status = f"🩸 DEMAND BROKE → Tgt 2: {d2_val:.1f}"
        sl = min(d1['top'] + (0.3 * atr_intra), c_close + (1.5 * atr_intra))
        target1 = c_close - 1.8 * (sl - c_close)
        target2 = min(d2_val, c_close - 2.5 * (sl - c_close))

    # Trigger 2: Equilibrium Zone Breakdown (Lost VAL + POC + VWAP with Bear Ribbon - like MAXHEALTH)
    elif c_close <= val and c_close <= poc_price and c_close <= vwap_val and is_bear_ribbon and not is_bear_trap:
        # Quality Gate: Suppress Short breakdowns if Daily timeframe is Bullish (Prevents WIPRO/BHARTIARTL traps)
        if daily_regime == "BULLISH":
            return {
                "is_valid": False,
                "ticker": ticker,
                "rejection_reason": f"SELL BLOCKED: #{ticker} Daily timeframe is BULLISH. Counter-trend breakdown suppressed."
            }
        # Quality Gate: Suppress breakdowns in narrow chop zones without volume expansion
        if va_width_pct < 0.90 and vol_mult < 1.25:
            return {
                "is_valid": False,
                "ticker": ticker,
                "rejection_reason": f"SELL BLOCKED: Narrow Equilibrium Box ({va_width_pct:.2f}% < 0.90%) without expansion volume ({vol_mult:.2f}x < 1.25x)."
            }
        detected_direction = "SELL"
        setup_type = "EQUILIBRIUM_VAL_BREAKDOWN"
        bo_status = f"🔴 EQUILIBRIUM BREAKDOWN → Demand 1: {d1['top']:.1f}"
        sl = max(vah, c_close + (1.2 * atr_intra))
        target1 = c_close - 1.8 * (sl - c_close)
        target2 = d1['center'] if d1['center'] < target1 else (c_close - 2.5 * (sl - c_close))

    # Trigger 3: Supply 1 Rejection Breakdown (Rejection of Supply ceiling + rejection wick)
    elif (c_high >= s1['bot'] and c_close <= s1['top']) and upper_wick_ratio >= 0.25 and c_close <= c_open:
        detected_direction = "SELL"
        setup_type = "SUPPLY_1_REJECTION_BREAKDOWN"
        bo_status = f"🛑 SUPPLY REJECTION → POC/Demand: {d1['top']:.1f}"
        sl = max(c_high, s1['top']) + (0.3 * atr_intra)
        target1 = c_close - 1.8 * (sl - c_close)
        target2 = d1['top'] if d1['top'] < target1 else (c_close - 2.5 * (sl - c_close))

    if not detected_direction:
        return {
            "is_valid": False,
            "ticker": ticker,
            "rejection_reason": f"IN EQUILIBRIUM: Price ({c_close:.2f}) inside balance zone [{val:.2f} - {vah:.2f}] with no clean breakout"
        }

    # If caller gave a required candidate_direction, enforce agreement
    if candidate_direction:
        cand_dir_clean = "BUY" if candidate_direction in ["BUY", "LONG", "B/O Call", "Long"] else "SELL"
        if cand_dir_clean != detected_direction:
            return {
                "is_valid": False,
                "ticker": ticker,
                "rejection_reason": f"DIRECTION MISMATCH: Candidate requested {cand_dir_clean}, but Vyuha BO structure confirmed {detected_direction} ({setup_type})"
            }

    risk_pts = abs(c_close - sl)
    
    # Enforce fine-tuned minimum & maximum risk limits (1.2x to 2.5x ATR)
    if risk_pts < (1.2 * atr_intra):
        sl = c_close - (1.2 * atr_intra) if detected_direction == "BUY" else c_close + (1.2 * atr_intra)
        risk_pts = abs(c_close - sl)
        target1 = c_close + (1.8 * risk_pts) if detected_direction == "BUY" else c_close - (1.8 * risk_pts)

    # Generate Option Contract Recommendation
    try:
        opt_rec = options.recommend_option_contract(
            ticker=ticker,
            direction=detected_direction,
            spot_entry=c_close,
            spot_sl=sl,
            spot_target1=target1,
            spot_target2=target2,
            demand_floor=d1['bot'],
            supply_ceiling=s1['top']
        )
    except Exception as e:
        opt_rec = None

    return {
        "is_valid": True,
        "ticker": ticker,
        "direction": detected_direction,
        "setup_type": setup_type,
        "bo_status": bo_status,
        "entry": round(c_close, 2),
        "sl": round(sl, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "risk_pts": round(risk_pts, 2),
        "zones": {
            "demand_1": f"[{d1['bot']:.1f} - {d1['top']:.1f}]",
            "demand_2": f"{d2_val:.1f}",
            "supply_1": f"[{s1['bot']:.1f} - {s1['top']:.1f}]",
            "supply_2": f"{s2_val:.1f}",
            "poc": round(poc_price, 2),
            "vah": round(vah, 2),
            "val": round(val, 2),
            "vwap": round(vwap_val, 2)
        },
        "option": opt_rec,
        "rejection_reason": None
    }


def run_vyuha_bo_workflow(send_telegram_fn=None, tickers=None, market_trend="NEUTRAL", force_rebuild=False):
    """
    Executes the complete Vyuha-BO Institutional Workflow:
    1. Sources candidates from cached swing symbols or provided universe
    2. Runs MTF zone ingestion (Daily) + High-resolution 15m FRVP Volume Engine
    3. Enforces BO Validation Gates (Blocks false buys under Supply or Bear Trend)
    4. Automatically updates Watchlist, MongoDB, and Paper Trades
    5. Dispatches formatted institutional Telegram alerts
    """
    load_workflow_alerts()
    print(f"\n=============================================================================")
    print(f"🏛️ RUNNING VYUHA-BO INSTITUTIONAL WORKFLOW @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Regime: {market_trend} | Force Universe Rebuild: {force_rebuild}")
    print(f"=============================================================================")

    if not tickers:
        from scanner import swing
        if force_rebuild or not os.path.exists(swing.ACTIVE_SWING_FILE):
            tickers = swing.filter_active_swing_universe()
        else:
            tickers = swing.load_active_swing_symbols()

    if not tickers:
        print("[WARNING] No tickers available to run Vyuha-BO workflow.")
        return []

    print(f"Ingesting MTF data for {len(tickers)} symbols...")
    ticker_ns_list = [t + ".NS" for t in tickers]

    # Ingest 15m and Daily data in batch
    try:
        df_all_15m = yf.download(ticker_ns_list, period="5d", interval="15m", group_by="ticker", progress=False, auto_adjust=True)
        df_all_daily = yf.download(ticker_ns_list, period="3mo", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
    except Exception as e:
        print(f"[ERROR] Batch download failed: {e}")
        return []

    validated_setups = []
    blocked_traps = []

    # Get active paper trades to avoid duplicate entries
    pt = trades.load_paper_trades()
    active_tickers = set([t["ticker"] for t in pt if t["status"] in ["ACTIVE", "PARTIAL_EXIT", "PENDING"]])

    for ticker in tickers:
        ticker_ns = ticker + ".NS"
        try:
            if len(tickers) == 1:
                df_15m = df_all_15m
                df_daily = df_all_daily
            else:
                if ticker_ns not in df_all_15m.columns.levels[0] or ticker_ns not in df_all_daily.columns.levels[0]:
                    continue
                df_15m = df_all_15m[ticker_ns].dropna()
                df_daily = df_all_daily[ticker_ns].dropna()

            if len(df_15m) < 30 or len(df_daily) < 15:
                continue

            res = validate_vyuha_bo_setup(ticker, df_15m, df_daily)
            
            if res["is_valid"]:
                # Check market regime alignment
                if market_trend == "BULLISH" and res["direction"] != "BUY":
                    continue
                if market_trend == "BEARISH" and res["direction"] != "SELL":
                    continue

                validated_setups.append(res)
                print(f"  [VALIDATED] {ticker}: {res['direction']} via {res['setup_type']} | {res['bo_status']}")
            else:
                if "BLOCKED" in res.get("rejection_reason", ""):
                    blocked_traps.append((ticker, res["rejection_reason"]))
                    print(f"  [BLOCKED TRAP] {ticker}: {res['rejection_reason']}")
        except Exception as e:
            continue

    print(f"\n--- Vyuha-BO Workflow Results ---")
    print(f"Symbols Scanned: {len(tickers)} | Validated: {len(validated_setups)} | Traps Prevented: {len(blocked_traps)}")

    # Process and record validated setups
    for s in validated_setups:
        ticker = s["ticker"]
        if ticker in active_tickers:
            continue

        alert_key = f"{ticker}_{s['direction']}_{s['setup_type']}_{datetime.now().strftime('%Y-%m-%d')}"
        if alert_key in alerted_workflow_today:
            continue

        alerted_workflow_today.add(alert_key)

        # 1. Initiate Paper Trade
        trades.initiate_paper_trade(ticker, {
            "entry": s["entry"],
            "sl": s["sl"],
            "target1": s["target1"],
            "target2": s["target2"],
            "direction": s["direction"],
            "setup_date": datetime.now().strftime("%Y-%m-%d"),
            "trade_type": "POSITIONAL"
        })

        # 2. Record to MongoDB & daily_recommendations.json
        try:
            db_storage.record_daily_recommendation(
                ticker=ticker,
                strategy="Vyuha-BO",
                direction=s["direction"],
                trade_type="POSITIONAL",
                entry=s["entry"],
                sl=s["sl"],
                target1=s["target1"],
                target2=s["target2"],
                reason=f"{s['bo_status']} ({s['setup_type']})",
                option_info=s.get("option")
            )
        except Exception as e:
            print(f"[ERROR] db_storage.record_daily_recommendation failed: {e}")

        # 3. Append to Watchlist
        dir_label = "B/O Call" if s["direction"] == "BUY" else "Short"
        proj_str = "PROJ_UP_1.5" if s["direction"] == "BUY" else "PROJ_DOWN_1.5"
        new_line = (
            f"{ticker} | Vyuha-BO | {dir_label} | POSITIONAL | "
            f"{s['entry']} | {proj_str} | {s['sl']} | {s['target1']} | {s['target2']}"
        )
        db_storage.append_to_watchlist(new_line)

        # 4. Dispatch Telegram Notification
        if send_telegram_fn:
            icon = "🟢" if s["direction"] == "BUY" else "🔴"
            action_verb = "BUY / CALL" if s["direction"] == "BUY" else "SELL / PUT"
            opt_block = s["option"]["formatted_block"] + "\n\n" if s.get("option") else ""
            msg = (
                f"🏛️ *VYUHA-BO INSTITUTIONAL SIGNAL: #{ticker}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• *Action:* {icon} {action_verb}\n"
                f"• *Setup:* `{s['setup_type']}`\n"
                f"• *Status:* {s['bo_status']}\n"
                f"• *Entry Level:* `{s['entry']:.2f}`\n"
                f"• *Stop Loss:* `{s['sl']:.2f}` (Risk: `{s['risk_pts']:.2f}` pts)\n"
                f"• *Target 1 (1:2 R:R):* `{s['target1']:.2f}`\n"
                f"• *Target 2 (HTF Zone):* `{s['target2']:.2f}`\n"
                f"• *FRVP POC:* `{s['zones']['poc']}` | *VWAP:* `{s['zones']['vwap']}`\n"
                f"• *Value Area:* `[{s['zones']['val']} - {s['zones']['vah']}]`\n"
                f"• *Demand 1:* `{s['zones']['demand_1']}` | *Supply 1:* `{s['zones']['supply_1']}`\n"
                f"• *Hold Duration:* 1 to 2 Days (Positional Swing)\n\n"
                f"{opt_block}"
            ).strip()
            try:
                send_telegram_fn(msg)
            except Exception as e:
                print(f"[ERROR] Failed to send Telegram alert: {e}")

    save_workflow_alerts()
    return validated_setups


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Vyuha-BO Institutional Workflow Runner")
    parser.add_argument("--symbol", type=str, help="Specific ticker to validate (e.g. MAXHEALTH)")
    parser.add_argument("--scan", action="store_true", help="Run full scheduled workflow scan")
    parser.add_argument("--force", action="store_true", help="Force universe rebuild")
    args = parser.parse_args()

    if args.symbol:
        sym = args.symbol.upper().replace(".NS", "")
        print(f"\n[INFO] Validating #{sym} through Vyuha-BO Engine...")
        df_i = yf.download(sym + ".NS", period="5d", interval="15m", progress=False, auto_adjust=True)
        df_d = yf.download(sym + ".NS", period="3mo", interval="1d", progress=False, auto_adjust=True)
        res = validate_vyuha_bo_setup(sym, df_i, df_d)
        print(json.dumps(res, indent=2))
    elif args.scan:
        run_vyuha_bo_workflow(force_rebuild=args.force)
    else:
        parser.print_help()
