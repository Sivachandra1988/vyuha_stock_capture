"""
=============================================================================
🏛️ UMX VYUHA — Institutional Multi-Timeframe Confluence & Clean Zone Scanner
Python Implementation of UMX_Vyuha_MTF_Confluence.pine
=============================================================================
Provides both Daily (EOD / Pre-Market) and Intraday (5m/15m) institutional
scanning capabilities:
1. Multi-Timeframe Ingestion (Monthly, Weekly, Daily Swings + PDH/PDL/PWH/PWL)
2. Proximity-Aware Confluence Clustering Engine (Demand 1/2, Supply 1/2)
3. High-Resolution 40-Bin FRVP Volume POC & 70% Value Area Engine
4. Zone-Anchored AVWAP & Velocity Slope Delta
5. Anti-Trap Wick Filters, CVD Proxy & Role Reversal Detection
6. Mitigation Test Counter (1st test vs 2nd+ full size)
=============================================================================
"""

import os
import sys
import json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

# Force UTF-8 output on Windows to prevent charmap codec errors with emoji characters
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

from scanner import core, trades

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

VYUHA_DAILY_WATCHLIST_FILE = os.path.join(DATA_DIR, "vyuha_daily_watchlist.json")
VYUHA_ALERTS_STATE_FILE = os.path.join(DATA_DIR, "vyuha_intraday_alerts.json")

alerted_vyuha_today = set()

def load_vyuha_alerts():
    global alerted_vyuha_today
    today_str = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(VYUHA_ALERTS_STATE_FILE):
        try:
            with open(VYUHA_ALERTS_STATE_FILE, "r") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    alerted_vyuha_today = set(data.get("alerts", []))
                    return
        except Exception:
            pass
    alerted_vyuha_today = set()

def save_vyuha_alerts():
    try:
        with open(VYUHA_ALERTS_STATE_FILE, "w") as f:
            json.dump({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "alerts": list(alerted_vyuha_today)
            }, f, indent=4)
    except Exception:
        pass


def clean_dataframe(df):
    """Flattens MultiIndex columns and ensures standard numeric OHLCV format."""
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        try:
            # If multi-index has ('Close', 'TICKER.NS'), extract level 0
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        except Exception:
            pass
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['Close'])
    return df

def calculate_atr(df, period=14):
    """Calculates Wilder's Average True Range (ATR)."""
    h_l = df['High'] - df['Low']
    h_pc = (df['High'] - df['Close'].shift(1)).abs()
    l_pc = (df['Low'] - df['Close'].shift(1)).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    return atr

def calculate_ema(series, length):
    """Calculates Exponential Moving Average (EMA)."""
    return series.ewm(span=length, adjust=False).mean()

def find_pivots(highs, lows, left_bars=4, right_bars=4):
    """Finds confirmed swing pivot highs and lows."""
    pivot_highs = []
    pivot_lows = []
    n = len(highs)
    for i in range(left_bars, n - right_bars):
        h = highs.iloc[i]
        l = lows.iloc[i]
        if all(h >= highs.iloc[i - left_bars:i]) and all(h >= highs.iloc[i + 1:i + 1 + right_bars]):
            pivot_highs.append(float(h))
        if all(l <= lows.iloc[i - left_bars:i]) and all(l <= lows.iloc[i + 1:i + 1 + right_bars]):
            pivot_lows.append(float(l))
    return pivot_highs, pivot_lows


# =============================================================================
# 2. PROXIMITY-AWARE CONFLUENCE CLUSTERING ENGINE
# =============================================================================

def find_confluence_cluster(levels, max_dist, ref_price):
    """
    Exact Python parity with Pine Script's findConfluenceCluster:
    Searches for level clusters where >= 2 levels converge within max_dist.
    Prioritizes the valid cluster CLOSEST to ref_price for Zone 1.
    """
    if len(levels) < 2:
        return None, None, None, 0

    best_centre = None
    best_min = None
    best_max = None
    best_count = 0
    best_dist_from_price = 1e12

    n = len(levels)
    for i in range(n):
        pivot = levels[i]
        cluster_members = [lvl for lvl in levels if abs(lvl - pivot) <= max_dist]
        cnt = len(cluster_members)
        if cnt >= 2:
            centre = sum(cluster_members) / cnt
            c_min = min(cluster_members)
            c_max = max(cluster_members)
            dist_from_price = abs(centre - ref_price)

            # Institutional Proximity Rule for Zone 1:
            # Valid cluster closer to price is preferred
            is_better = (
                best_centre is None
                or (dist_from_price < best_dist_from_price - (max_dist * 0.15))
                or (abs(dist_from_price - best_dist_from_price) <= (max_dist * 0.15) and cnt > best_count)
            )

            if is_better:
                best_count = cnt
                best_centre = centre
                best_min = c_min
                best_max = c_max
                best_dist_from_price = dist_from_price

    return best_centre, best_min, best_max, best_count


def calculate_vyuha_zones(df_daily, current_price, current_atr):
    """
    Discovers Institutional Demand & Supply Zones using:
    - Benchmarks: PDH, PDL, PDO, PDC, PWH, PWL, PMH, PML
    - Swings: Daily (88 bars), Weekly, Monthly
    - Confluence clustering within 2.5 * ATR
    - Zone 2 Expansion Targets & Deduplication Guard
    """
    df_daily = clean_dataframe(df_daily)
    if len(df_daily) < 15:
        return None

    atr = max(float(current_atr), float(current_price) * 0.005)
    max_clust_dist = 2.5 * atr

    # 1. Benchmarks
    pdh = float(df_daily['High'].iloc[-2]) if len(df_daily) >= 2 else float(df_daily['High'].iloc[-1])
    pdl = float(df_daily['Low'].iloc[-2]) if len(df_daily) >= 2 else float(df_daily['Low'].iloc[-1])
    pdo = float(df_daily['Open'].iloc[-2]) if len(df_daily) >= 2 else float(df_daily['Open'].iloc[-1])
    pdc = float(df_daily['Close'].iloc[-2]) if len(df_daily) >= 2 else float(df_daily['Close'].iloc[-1])

    # Weekly & Monthly Benchmarks (approx 5 and 22 bars)
    pwh = float(df_daily['High'].iloc[-6:-1].max()) if len(df_daily) >= 6 else pdh
    pwl = float(df_daily['Low'].iloc[-6:-1].min()) if len(df_daily) >= 6 else pdl

    pmh = float(df_daily['High'].iloc[-23:-1].max()) if len(df_daily) >= 23 else pwh
    pml = float(df_daily['Low'].iloc[-23:-1].min()) if len(df_daily) >= 23 else pwl

    # Daily swings over last 88 bars
    lookback_bars = min(len(df_daily) - 1, 88)
    sub_df = df_daily.iloc[-lookback_bars:]
    piv_h, piv_l = find_pivots(sub_df['High'], sub_df['Low'], left_bars=3, right_bars=3)

    daily_high_extreme = float(sub_df['High'].max())
    daily_low_extreme = float(sub_df['Low'].min())

    all_res_candidates = [pdh, pdo, pdc, pwh, pmh, daily_high_extreme] + piv_h
    all_sup_candidates = [pdl, pdo, pdc, pwl, pml, daily_low_extreme] + piv_l

    # Filter strictly overhead (> price) and strictly underlying (< price)
    buffer = 0.05 * atr
    res_levels = [float(lvl) for lvl in all_res_candidates if float(lvl) >= current_price + buffer]
    sup_levels = [float(lvl) for lvl in all_sup_candidates if float(lvl) <= current_price - buffer]

    # Confluence Clustering
    res_center, res_min, res_max, res_cnt = find_confluence_cluster(res_levels, max_clust_dist, current_price)
    sup_center, sup_min, sup_max, sup_cnt = find_confluence_cluster(sup_levels, max_clust_dist, current_price)

    # Fallback to nearest standalone level if no multi-level cluster
    if res_center is None:
        above = [lvl for lvl in res_levels if lvl >= current_price]
        res_center = min(above) if above else current_price + (1.5 * atr)
        res_min = res_center - (0.2 * atr)
        res_max = res_center + (0.2 * atr)
        res_cnt = 1

    if sup_center is None:
        below = [lvl for lvl in sup_levels if lvl <= current_price]
        sup_center = max(below) if below else current_price - (1.5 * atr)
        sup_min = sup_center - (0.2 * atr)
        sup_max = sup_center + (0.2 * atr)
        sup_cnt = 1

    # Wick Envelopment padding: zone height of ~0.8 * ATR
    zone_height = 0.4 * atr
    sup_zone_top = min(current_price - 0.01, max(sup_max, sup_center + zone_height * 0.5))
    sup_zone_bot = min(sup_min, sup_center - zone_height * 0.5)

    res_zone_top = max(res_max, res_center + zone_height * 0.5)
    res_zone_bot = max(current_price + 0.01, min(res_min, res_center - zone_height * 0.5))

    # Deduplication & Zone 2 Projection:
    # Zone 2 must be separated by >= 1.5 ATR from Zone 1
    far_res = [lvl for lvl in res_levels if lvl > res_zone_top + (1.5 * atr)]
    supply_2 = min(far_res) if far_res else res_zone_top + (2.5 * atr)

    far_sup = [lvl for lvl in sup_levels if lvl < sup_zone_bot - (1.5 * atr)]
    demand_2 = max(far_sup) if far_sup else sup_zone_bot - (2.5 * atr)

    # 50% Retracement Band (Golden Zone) inside Zone 1
    dem_retrace_mid = (sup_zone_top + sup_zone_bot) / 2.0
    dem_retrace_sl = sup_zone_bot - (0.5 * atr)

    sup_retrace_mid = (res_zone_top + res_zone_bot) / 2.0
    sup_retrace_sl = res_zone_top + (0.5 * atr)

    # Squeeze / Chop check: distance between Demand Top and Supply Bottom < 0.8 ATR
    is_squeeze = (res_zone_bot - sup_zone_top) < (0.8 * atr)

    return {
        "demand_1": {"top": sup_zone_top, "bot": sup_zone_bot, "center": sup_center, "count": sup_cnt},
        "demand_2": demand_2,
        "supply_1": {"top": res_zone_top, "bot": res_zone_bot, "center": res_center, "count": res_cnt},
        "supply_2": supply_2,
        "dem_retrace": {"mid": dem_retrace_mid, "sl": dem_retrace_sl},
        "sup_retrace": {"mid": sup_retrace_mid, "sl": sup_retrace_sl},
        "is_squeeze": is_squeeze,
        "pdh": pdh,
        "pdl": pdl,
        "pwh": pwh,
        "pwl": pwl
    }


# =============================================================================
# 3. HIGH-RESOLUTION 40-BIN FRVP VOLUME POC & 70% VALUE AREA ENGINE
# =============================================================================

def calculate_frvp_poc(df_slice, num_bins=40):
    """
    Computes 40-Bin Fixed Range Volume Profile (FRVP):
    - Distributes continuous candle volume across spanned price bins.
    - Determines Volume POC (Point of Control) and 70% Value Area (VAH, VAL).
    """
    df_slice = clean_dataframe(df_slice)
    if len(df_slice) < 5:
        return None, None, None

    p_high = float(df_slice['High'].max())
    p_low = float(df_slice['Low'].min())
    if p_high <= p_low:
        return p_high, p_high, p_low

    bin_width = (p_high - p_low) / num_bins
    vol_bins = np.zeros(num_bins)

    for _, row in df_slice.iterrows():
        b_low = float(row['Low'])
        b_high = float(row['High'])
        b_vol = float(row['Volume'])
        if b_high < b_low or b_vol <= 0:
            continue

        start_bin = int(np.clip((b_low - p_low) / bin_width, 0, num_bins - 1))
        end_bin = int(np.clip((b_high - p_low) / bin_width, 0, num_bins - 1))
        span = max(1, end_bin - start_bin + 1)
        per_bin_vol = b_vol / span

        for b in range(start_bin, end_bin + 1):
            vol_bins[b] += per_bin_vol

    poc_idx = int(np.argmax(vol_bins))
    poc_price = p_low + (poc_idx + 0.5) * bin_width

    # 70% Value Area Dual Expansion
    total_vol = np.sum(vol_bins)
    target_vol = 0.70 * total_vol
    curr_vol = vol_bins[poc_idx]
    up_idx = poc_idx
    dn_idx = poc_idx

    while curr_vol < target_vol and (up_idx < num_bins - 1 or dn_idx > 0):
        next_up_vol = vol_bins[up_idx + 1] if up_idx < num_bins - 1 else 0
        next_dn_vol = vol_bins[dn_idx - 1] if dn_idx > 0 else 0

        if next_up_vol >= next_dn_vol and up_idx < num_bins - 1:
            up_idx += 1
            curr_vol += vol_bins[up_idx]
        elif dn_idx > 0:
            dn_idx -= 1
            curr_vol += vol_bins[dn_idx]
        elif up_idx < num_bins - 1:
            up_idx += 1
            curr_vol += vol_bins[up_idx]
        else:
            break

    val_price = p_low + dn_idx * bin_width
    vah_price = p_low + (up_idx + 1) * bin_width

    return poc_price, vah_price, val_price


# =============================================================================
# 4. ZONE-ANCHORED AVWAP & VELOCITY SLOPE DELTA
# =============================================================================

def calculate_zone_avwap(df_slice):
    """
    Calculates Anchored Volume Weighted Average Price (AVWAP) and 3-bar smoothed slope delta.
    Returns: (latest_avwap, slope_delta, is_diamond_green)
    """
    df_slice = clean_dataframe(df_slice)
    if len(df_slice) < 5:
        return None, 0.0, False

    pv = (df_slice['Close'] * df_slice['Volume']).cumsum()
    cum_vol = df_slice['Volume'].cumsum()
    avwap_series = pv / cum_vol.replace(0, np.nan)

    latest_avwap = float(avwap_series.iloc[-1])
    slope_delta = float(avwap_series.iloc[-1] - avwap_series.iloc[-4]) if len(avwap_series) >= 4 else 0.0
    is_diamond_green = slope_delta > 0

    return latest_avwap, slope_delta, is_diamond_green


# =============================================================================
# 5. ANTI-TRAP WICK & CVD PROXY EXECUTION GATES
# =============================================================================

def evaluate_anti_trap_gates(candle):
    """
    Evaluates Wick Absorption & Fakeout Traps:
    - Upper wick > 35% of range is Bull Trap (blocks Long).
    - Lower wick > 35% of range is Bear Trap (blocks Short).
    """
    c_open = float(candle['Open'])
    c_close = float(candle['Close'])
    c_high = float(candle['High'])
    c_low = float(candle['Low'])

    total_range = max(c_high - c_low, 0.001)
    body_top = max(c_open, c_close)
    body_bot = min(c_open, c_close)

    upper_wick = c_high - body_top
    lower_wick = body_bot - c_low

    is_bull_trap = (upper_wick / total_range) > 0.35
    is_bear_trap = (lower_wick / total_range) > 0.35

    return is_bull_trap, is_bear_trap, lower_wick / total_range, upper_wick / total_range


# =============================================================================
# 6. DAILY (EOD / PRE-MARKET) VYUHA SCANNER
# =============================================================================

def run_vyuha_daily_scan(send_telegram_fn=None, custom_tickers=None):
    """
    Scans stocks on the Daily timeframe to find symbols approaching or interacting
    with high-confluence institutional Action Zones (Demand 1 or Supply 1).
    Produces a curated Watchlist for the next trading session.
    """
    tickers = custom_tickers if custom_tickers else core.get_dynamic_tickers()
    if not tickers:
        print("[VYUHA DAILY] No tickers found.")
        return []

    print(f"\n🏛️ --- [VYUHA] Running Daily Institutional Confluence Scan ({len(tickers)} stocks) ---")
    watchlist_candidates = []

    # Batch download daily data (1 year)
    ticker_ns_list = [t + ".NS" for t in tickers]
    try:
        df_all = yf.download(ticker_ns_list, period="1y", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
    except Exception as e:
        print(f"[VYUHA DAILY] Download error: {e}")
        return []

    for ticker in tickers:
        try:
            ticker_ns = ticker + ".NS"
            if len(tickers) == 1:
                df = df_all.dropna()
            else:
                if ticker_ns not in df_all.columns.levels[0]:
                    continue
                df = df_all[ticker_ns].dropna()

            df = clean_dataframe(df)
            if len(df) < 50:
                continue

            # Indicators
            df['ATR_14'] = calculate_atr(df, 14)
            df['EMA_9'] = calculate_ema(df['Close'], 9)
            df['EMA_21'] = calculate_ema(df['Close'], 21)

            latest = df.iloc[-1]
            prev = df.iloc[-2]
            c_close = float(latest['Close'])
            atr_val = float(latest['ATR_14'])
            if atr_val <= 0:
                continue

            # Calculate VYUHA Zones
            zones = calculate_vyuha_zones(df, c_close, atr_val)
            if not zones or zones['is_squeeze']:
                continue

            d1 = zones['demand_1']
            s1 = zones['supply_1']

            # Confluence Proximity Criteria:
            # 1. Price is testing inside or within 0.75 ATR of Demand 1
            is_at_demand = (c_close >= d1['bot'] - (0.2 * atr_val)) and (c_close <= d1['top'] + (0.75 * atr_val))
            # 2. Price is testing inside or within 0.75 ATR of Supply 1
            is_at_supply = (c_close <= s1['top'] + (0.2 * atr_val)) and (c_close >= s1['bot'] - (0.75 * atr_val))

            # Trend Start Crossover on Daily
            ema9_prev, ema21_prev = float(prev['EMA_9']), float(prev['EMA_21'])
            ema9_curr, ema21_curr = float(latest['EMA_9']), float(latest['EMA_21'])
            is_bull_cloud_flip = (ema9_prev <= ema21_prev) and (ema9_curr > ema21_curr)
            is_bear_cloud_flip = (ema9_prev >= ema21_prev) and (ema9_curr < ema21_curr)

            # Role Reversal Check (Broken PDL / PDH test)
            pdl = zones['pdl']
            pdh = zones['pdh']
            is_flipped_demand = (float(prev['Close']) < pdl) and (c_close >= pdl * 0.98 and c_close <= pdl * 1.02)
            is_flipped_supply = (float(prev['Close']) > pdh) and (c_close >= pdh * 0.98 and c_close <= pdh * 1.02)

            setup_type = None
            bias = None

            if is_at_demand or is_bull_cloud_flip or is_flipped_supply:
                bias = "LONG"
                if is_at_demand:
                    setup_type = "DEMAND_1_CONFLUENCE"
                elif is_bull_cloud_flip:
                    setup_type = "DAILY_TREND_START_BULL"
                else:
                    setup_type = "FLIPPED_DEMAND_TEST"

            elif is_at_supply or is_bear_cloud_flip or is_flipped_demand:
                bias = "SHORT"
                if is_at_supply:
                    setup_type = "SUPPLY_1_CONFLUENCE"
                elif is_bear_cloud_flip:
                    setup_type = "DAILY_TREND_START_BEAR"
                else:
                    setup_type = "FLIPPED_SUPPLY_TEST"

            if bias:
                # FRVP POC on last 60 daily bars
                poc_price, vah, val = calculate_frvp_poc(df.tail(60))
                item = {
                    "ticker": ticker,
                    "bias": bias,
                    "setup": setup_type,
                    "close": round(c_close, 2),
                    "atr": round(atr_val, 2),
                    "demand_1": [round(d1['bot'], 2), round(d1['top'], 2)],
                    "supply_1": [round(s1['bot'], 2), round(s1['top'], 2)],
                    "demand_2": round(zones['demand_2'], 2),
                    "supply_2": round(zones['supply_2'], 2),
                    "vol_poc": round(poc_price, 2) if poc_price else None,
                    "date": datetime.now().strftime("%Y-%m-%d")
                }
                watchlist_candidates.append(item)
        except Exception as e:
            continue

    # Save to JSON
    with open(VYUHA_DAILY_WATCHLIST_FILE, "w") as f:
        json.dump(watchlist_candidates, f, indent=4)

    print(f"🎯 [VYUHA DAILY] Completed. Found {len(watchlist_candidates)} institutional action candidates.")

    if send_telegram_fn and watchlist_candidates:
        top_longs = [c for c in watchlist_candidates if c['bias'] == 'LONG'][:4]
        top_shorts = [c for c in watchlist_candidates if c['bias'] == 'SHORT'][:4]

        msg = "🏛️ *UMX VYUHA — DAILY INSTITUTIONAL WATCHLIST*\n"
        msg += f"📅 Date: `{datetime.now().strftime('%Y-%m-%d')}`\n\n"
        if top_longs:
            msg += "🟢 *BULLISH ACTION ZONES (Demand 1 / Retrace)*:\n"
            for c in top_longs:
                msg += f"• *{c['ticker']}* @ `{c['close']}` | Demand: `{c['demand_1'][0]}-{c['demand_1'][1]}` | POC: `{c['vol_poc']}`\n"
            msg += "\n"
        if top_shorts:
            msg += "🔴 *BEARISH ACTION ZONES (Supply 1 / Retrace)*:\n"
            for c in top_shorts:
                msg += f"• *{c['ticker']}* @ `{c['close']}` | Supply: `{c['supply_1'][0]}-{c['supply_1'][1]}` | POC: `{c['vol_poc']}`\n"

        send_telegram_fn(msg)

    return watchlist_candidates


# =============================================================================
# 7. INTRADAY (5m / 15m) LIVE VYUHA SCANNER
# =============================================================================

def evaluate_intraday_vyuha_setup(ticker, df_intraday, df_daily):
    """
    Evaluates real-time 15m candle against VYUHA Execution Gates:
    1. Demand 1 / Supply 1 Reaction
    2. Golden 50% Retracement rejection wick (> 35%)
    3. Zone-Anchored AVWAP with Velocity Slope Diamond confirmation
    4. 9/21 EMA Cloud direction
    5. Anti-Fakeout & Trap Wick suppression
    """
    df_intraday = clean_dataframe(df_intraday)
    df_daily = clean_dataframe(df_daily)
    if len(df_intraday) < 30 or len(df_daily) < 15:
        return None

    # Calculate Daily Confluence Zones
    c_close = float(df_intraday['Close'].iloc[-1])
    atr_daily = calculate_atr(df_daily, 14).iloc[-1]
    zones = calculate_vyuha_zones(df_daily, c_close, atr_daily)
    if not zones or zones['is_squeeze']:
        return None

    # Indicators on Intraday
    df_intraday = df_intraday.copy()
    df_intraday['ATR_14'] = calculate_atr(df_intraday, 14)
    df_intraday['EMA_9'] = calculate_ema(df_intraday['Close'], 9)
    df_intraday['EMA_21'] = calculate_ema(df_intraday['Close'], 21)

    latest = df_intraday.iloc[-1]
    prev = df_intraday.iloc[-2]
    c_close = float(latest['Close'])
    c_open = float(latest['Open'])
    c_high = float(latest['High'])
    c_low = float(latest['Low'])
    atr_intra = float(latest['ATR_14'])
    if atr_intra <= 0:
        return None

    is_bull_trap, is_bear_trap, lower_wick_ratio, upper_wick_ratio = evaluate_anti_trap_gates(latest)

    # 40-bin FRVP POC from last 40 intraday bars
    poc_price, vah, val = calculate_frvp_poc(df_intraday.tail(40))

    # Zone AVWAP from origin of current move (last 20 bars)
    avwap_val, avwap_slope, is_green_diamond = calculate_zone_avwap(df_intraday.tail(20))

    ema9 = float(latest['EMA_9'])
    ema21 = float(latest['EMA_21'])
    is_ema_bull = ema9 > ema21
    is_ema_bear = ema9 < ema21

    d1 = zones['demand_1']
    s1 = zones['supply_1']

    # --- EXECUTION LOGIC ---
    # 🟢 LONG SETUP: Price in Demand 1 / Retrace Band + Lower Wick Rejection >= 30% + Green Diamond + Not Bull Trap
    in_demand_zone = (c_low <= d1['top'] and c_close >= d1['bot'])
    demand_rejection = lower_wick_ratio >= 0.30 and c_close > c_open
    avwap_bull_confirm = (c_close >= avwap_val) if avwap_val else True

    if in_demand_zone and demand_rejection and not is_bull_trap and avwap_bull_confirm:
        entry = c_close
        sl = zones['dem_retrace']['sl']
        risk = entry - sl
        if risk <= 0 or risk > (3.0 * atr_intra):
            sl = entry - (1.2 * atr_intra)
            risk = entry - sl

        t1 = entry + (2.0 * risk)
        t2 = zones['supply_1']['bot']
        if t2 <= entry:
            t2 = entry + (3.0 * risk)

        return {
            "ticker": ticker,
            "direction": "BUY",
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "target1": round(t1, 2),
            "target2": round(t2, 2),
            "risk_pts": round(risk, 2),
            "zone": f"Demand 1 [{d1['bot']:.1f} - {d1['top']:.1f}]",
            "diamond": "🟢 Green ▲" if is_green_diamond else "⚪ Neutral",
            "poc": round(poc_price, 2) if poc_price else round(entry, 2),
            "setup": "DEMAND_RETRACE_BOUNCE",
            "msg": (
                f"🚀 *UMX VYUHA INTRADAY BUY: #{ticker}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🎯 *Zone:* `Demand 1 [{d1['bot']:.1f} - {d1['top']:.1f}]`\n"
                f"⚡ *Entry:* `{entry:.2f}` (Rejection Wick: `{lower_wick_ratio*100:.0f}%`)\n"
                f"🛑 *Stop-Loss:* `{sl:.2f}` (Risk: `{risk:.2f}` pts)\n"
                f"🎯 *Target 1 (1:2):* `{t1:.2f}`\n"
                f"🏁 *Target 2 (Supply 1):* `{t2:.2f}`\n"
                f"💎 *AVWAP Slope:* `{'🟢 Accumulation ▲' if is_green_diamond else '⚪ Neutral'}`\n"
                f"⚡ *Volume POC:* `{poc_price:.2f}`"
            )
        }

    # 🔴 SHORT SETUP: Price in Supply 1 / Retrace Band + Upper Wick Rejection >= 30% + Red Diamond + Not Bear Trap
    in_supply_zone = (c_high >= s1['bot'] and c_close <= s1['top'])
    supply_rejection = upper_wick_ratio >= 0.30 and c_close < c_open
    avwap_bear_confirm = (c_close <= avwap_val) if avwap_val else True

    if in_supply_zone and supply_rejection and not is_bear_trap and avwap_bear_confirm:
        entry = c_close
        sl = zones['sup_retrace']['sl']
        risk = sl - entry
        if risk <= 0 or risk > (3.0 * atr_intra):
            sl = entry + (1.2 * atr_intra)
            risk = sl - entry

        t1 = entry - (2.0 * risk)
        t2 = zones['demand_1']['top']
        if t2 >= entry:
            t2 = entry - (3.0 * risk)

        return {
            "ticker": ticker,
            "direction": "SELL",
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "target1": round(t1, 2),
            "target2": round(t2, 2),
            "risk_pts": round(risk, 2),
            "zone": f"Supply 1 [{s1['bot']:.1f} - {s1['top']:.1f}]",
            "diamond": "🔴 Red ▼" if not is_green_diamond else "⚪ Neutral",
            "poc": round(poc_price, 2) if poc_price else round(entry, 2),
            "setup": "SUPPLY_RETRACE_REJECTION",
            "msg": (
                f"🩸 *UMX VYUHA INTRADAY SHORT: #{ticker}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🎯 *Zone:* `Supply 1 [{s1['bot']:.1f} - {s1['top']:.1f}]`\n"
                f"⚡ *Entry:* `{entry:.2f}` (Rejection Wick: `{upper_wick_ratio*100:.0f}%`)\n"
                f"🛑 *Stop-Loss:* `{sl:.2f}` (Risk: `{risk:.2f}` pts)\n"
                f"🎯 *Target 1 (1:2):* `{t1:.2f}`\n"
                f"🏁 *Target 2 (Demand 1):* `{t2:.2f}`\n"
                f"💎 *AVWAP Slope:* `{'🔴 Distribution ▼' if not is_green_diamond else '⚪ Neutral'}`\n"
                f"⚡ *Volume POC:* `{poc_price:.2f}`"
            )
        }

    return None


def execute_vyuha_intraday_scan(send_telegram_fn=None, market_trend="NEUTRAL"):
    """
    Executes the scheduled Intraday VYUHA scan:
    - Reads from the Daily Watchlist first (or dynamic tickers)
    - Fetches 15m intraday bars and executes the Anti-Trap & Zone gates
    - Sends Telegram notification
    """
    load_vyuha_alerts()

    # Prioritize watchlist created by daily scan if available
    tickers = []
    if os.path.exists(VYUHA_DAILY_WATCHLIST_FILE):
        try:
            with open(VYUHA_DAILY_WATCHLIST_FILE, "r") as f:
                wl = json.load(f)
                tickers = [item['ticker'] for item in wl]
        except Exception:
            pass

    if not tickers:
        tickers = core.get_dynamic_tickers()[:30]

    if not tickers:
        print("[VYUHA INTRADAY] No tickers to scan.")
        return []

    print(f"\n⚡ --- [VYUHA] Running Intraday 15m Confluence Scan @ {datetime.now().strftime('%H:%M:%S')} ({len(tickers)} stocks) ---")

    ticker_ns_list = [t + ".NS" for t in tickers]
    try:
        df_intra_all = yf.download(ticker_ns_list, period="5d", interval="15m", group_by="ticker", progress=False, auto_adjust=True)
        df_daily_all = yf.download(ticker_ns_list, period="6mo", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
    except Exception as e:
        print(f"[VYUHA INTRADAY] Download error: {e}")
        return []

    candidates = []
    for ticker in tickers:
        try:
            ticker_ns = ticker + ".NS"
            if len(tickers) == 1:
                df_intra = df_intra_all.dropna()
                df_daily = df_daily_all.dropna()
            else:
                if ticker_ns not in df_intra_all.columns.levels[0] or ticker_ns not in df_daily_all.columns.levels[0]:
                    continue
                df_intra = df_intra_all[ticker_ns].dropna()
                df_daily = df_daily_all[ticker_ns].dropna()

            res = evaluate_intraday_vyuha_setup(ticker, df_intra, df_daily)
            if res:
                if market_trend == "BEARISH" and res["direction"] != "SELL":
                    continue
                if market_trend == "BULLISH" and res["direction"] != "BUY":
                    continue
                candidates.append(res)
        except Exception:
            continue

    pt = trades.load_paper_trades()
    active_tickers = set([t["ticker"] for t in pt if t["status"] in ["ACTIVE", "PARTIAL_EXIT", "PENDING"]])

    for c in candidates:
        ticker = c["ticker"]
        if ticker in active_tickers:
            continue

        alert_key = f"{ticker}_{c['setup']}_{datetime.now().strftime('%Y-%m-%d')}"
        if alert_key not in alerted_vyuha_today:
            alerted_vyuha_today.add(alert_key)
            save_vyuha_alerts()
            print(f"[VYUHA ALERT] Fired for {ticker} ({c['direction']})")
            if send_telegram_fn:
                send_telegram_fn(c["msg"])

    return candidates
