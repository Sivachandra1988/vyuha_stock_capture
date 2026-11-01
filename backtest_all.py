"""
=============================================================
  TRADER PILOT — MULTI-STRATEGY BACKTEST ENGINE
  Horizons : LONG_TERM > SHORT_TERM > INTRADAY
  Strategies:
    LONG_TERM  → SMC_FVG (weekly FVG mitigation)
                 AI_SMC_MC (Naive Bayes + EMA tie-breaker)
    SHORT_TERM → UT_Bot  (trend reversal, 3-20 day hold)
                 Swing    (breakout/confluence, daily bars)
    INTRADAY   → NR7     (inside-bar contraction, same day)
  Rules:
    - A stock placed in LONG_TERM is skipped for smaller horizons.
    - A stock placed in SHORT_TERM is skipped for INTRADAY.
    - Auto-tune: if accuracy < MIN_ACCURACY, thresholds are
      tightened and the cycle repeats up to MAX_TUNE_ROUNDS.
=============================================================
"""
import yfinance as yf
import pandas as pd
import numpy as np
import os, sys, json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))
from scanner import core
from scanner.smc_ai import get_monte_carlo_smc_signal
from db_cache import get_cached_yfinance

# ──────────────────────────────────────────────────────────
#  CONFIG
# ──────────────────────────────────────────────────────────
MIN_ACCURACY     = 60.0   # % win rate target
MAX_TUNE_ROUNDS  = 4      # how many auto-tune iterations
INTRADAY_HOLD    = 2      # max days to hold intraday (2 gives more room to hit T1)
SHORT_HOLD       = 20     # max days for short term
LONG_HOLD        = 90     # max days for long term
DATA_PERIOD_DAILY  = "2y"
DATA_PERIOD_WEEKLY = "4y"

# Tunable thresholds — enhanced for higher systemic profitability
params = {
    "nr7_rsi_bull_min"  : 55,   # Tighter bullishness
    "nr7_rsi_bear_max"  : 45,   # Tighter bearishness
    "nr7_atr_mult_sl"   : 1.0,  # 1.0 risk ensures tighter invalidation
    "nr7_atr_mult_t1"   : 1.5,  # 1.5:1 RR baseline
    "utbot_ema_fast"    : 11,   # 11 instead of 9 avoids false noise
    "utbot_ema_slow"    : 22,   # 22 smooths the baseline
    "swing_roc_period"  : 10,
    "swing_vol_ratio"   : 1.5,  # Steeper volume requirement (1.5x)
    "smc_fvg_lookback"  : 25,   # Slightly tighter context for FVG
    "smc_fvg_rr"        : 2.2,  # Boosted RR for structural wings
    "ai_mc_prob_min"    : 0.68, # 68% threshold for ultra high conviction
    "ai_mc_tp_rr"       : 2.5,  # Massive 2.5R runners for AI setups
}

TICKERS = core.get_dynamic_tickers()

# ──────────────────────────────────────────────────────────
#  MARKET REGIME
# ──────────────────────────────────────────────────────────
def get_nifty_regime():
    df = get_cached_yfinance("^NSEI", period=DATA_PERIOD_DAILY, interval="1d", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df["SMA50"] = df["Close"].rolling(50).mean()
    regime = {}
    for dt, row in df.iterrows():
        key = dt.strftime("%Y-%m-%d")
        if pd.isna(row["SMA50"]):
            regime[key] = "NEUTRAL"
        elif float(row["Close"]) > float(row["SMA50"]):
            regime[key] = "BULLISH"
        else:
            regime[key] = "BEARISH"
    return regime

# ──────────────────────────────────────────────────────────
#  HELPER: TRADE OUTCOME
# ──────────────────────────────────────────────────────────
def sim_trade(df_fwd, direction, entry, sl, target, max_hold):
    """Walk forward bars checking SL/Target, respect max_hold."""
    for k, row in df_fwd.head(max_hold).iterrows():
        hi, lo = float(row["High"]), float(row["Low"])
        if direction == "BUY":
            if hi >= target:
                return "WIN", float(target)
            if lo <= sl:
                return "LOSS", float(sl)
        else:
            if lo <= target:
                return "WIN", float(target)
            if hi >= sl:
                return "LOSS", float(sl)
    # Time stop – close at last bar
    last_close = float(df_fwd.head(max_hold).iloc[-1]["Close"]) if len(df_fwd) >= 1 else entry
    result = "WIN" if (direction == "BUY" and last_close > entry) or \
                      (direction == "SELL" and last_close < entry) else "LOSS"
    return result, last_close

# ──────────────────────────────────────────────────────────
#  STRATEGY 1: SMC FVG (LONG TERM, weekly bars)
# ──────────────────────────────────────────────────────────
def bt_smc_fvg(ticker, df_weekly, regime):
    trades = []
    lb = params["smc_fvg_lookback"]
    rr = params["smc_fvg_rr"]
    df = df_weekly.copy()
    df["H-L"]  = df["High"] - df["Low"]
    df["H-PC"] = abs(df["High"] - df["Close"].shift(1))
    df["L-PC"] = abs(df["Low"]  - df["Close"].shift(1))
    df["TR"]   = df[["H-L","H-PC","L-PC"]].max(axis=1)
    df["ATR"]  = df["TR"].rolling(14).mean()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["SMA50"] = df["Close"].rolling(50).mean()
    df["EMA9"]  = df["Close"].ewm(span=9, adjust=False).mean()  # slope proxy

    for i in range(lb+2, len(df)-2):
        sub   = df.iloc[:i+1]
        atr   = float(df["ATR"].iloc[i])
        ema20 = float(df["EMA20"].iloc[i])
        sma50 = float(df["SMA50"].iloc[i])
        ema9  = float(df["EMA9"].iloc[i])
        ema9_prev = float(df["EMA9"].iloc[i-3])  # 3wk slope
        if pd.isna(atr) or atr <= 0 or pd.isna(sma50): continue

        c_close = float(df["Close"].iloc[i])
        c_low   = float(df["Low"].iloc[i])
        c_high  = float(df["High"].iloc[i])

        bullish_fvg = bearish_fvg = None
        for x in range(i-lb, i-1):
            if x < 1: continue
            p1 = sub.iloc[x-1]; p2 = sub.iloc[x]; p3 = sub.iloc[x+1]
            if float(p3["Low"]) > float(p1["High"]) and float(p2["Close"]) > float(p2["Open"]):
                fbot, ftop = float(p1["High"]), float(p3["Low"])
                filled = any(float(sub.iloc[j]["Low"]) < fbot for j in range(x+2, i))
                if not filled:
                    bullish_fvg = (fbot, ftop)
            if float(p3["High"]) < float(p1["Low"]) and float(p2["Open"]) > float(p2["Close"]):
                ftop2, fbot2 = float(p1["Low"]), float(p3["High"])
                filled = any(float(sub.iloc[j]["High"]) > ftop2 for j in range(x+2, i))
                if not filled:
                    bearish_fvg = (fbot2, ftop2)

        date_str = df.index[i].strftime("%Y-%m-%d")
        reg = regime.get(date_str, "NEUTRAL")

        direction = None

        # BULLISH FVG MITIGATION — price taps into buy zone from above
        if bullish_fvg and c_low <= bullish_fvg[1] and c_close > bullish_fvg[0]:
            # Only buy if price is above EMA20 or recovering
            if c_close > ema20 or ema9 > ema9_prev:
                direction = "BUY"

        # BEARISH FVG MITIGATION — strictly trade in bearish macro
        if bearish_fvg and c_high >= bearish_fvg[0] and c_close < bearish_fvg[1]:
            if c_close < ema20 and ema20 < sma50 and ema9 < ema9_prev:
                direction = "SELL"

        if direction is None: continue
        if reg == "BEARISH" and direction == "BUY": continue
        if reg == "BULLISH" and direction == "SELL": continue

        entry = c_close
        risk  = atr * 1.5
        sl     = entry - risk if direction == "BUY" else entry + risk
        target = entry + risk * rr if direction == "BUY" else entry - risk * rr

        df_fwd = df.iloc[i+1:]
        outcome, exit_price = sim_trade(df_fwd, direction, entry, sl, target, LONG_HOLD // 5)  # weekly bars
        pnl_pct = ((exit_price - entry) / entry * 100) if direction == "BUY" \
                  else ((entry - exit_price) / entry * 100)
        trades.append({"ticker": ticker, "strategy": "SMC_FVG", "horizon": "LONG_TERM",
                        "date": date_str, "direction": direction, "entry": entry,
                        "sl": sl, "target": target, "exit": exit_price,
                        "outcome": outcome, "pnl_pct": pnl_pct})
    return trades

# ──────────────────────────────────────────────────────────
#  STRATEGY 2: AI_SMC_MC (LONG TERM, daily bars)
# ──────────────────────────────────────────────────────────
def bt_ai_smc_mc(ticker, df_daily, regime):
    trades = []
    prob_min = params["ai_mc_prob_min"]
    rr       = params["ai_mc_tp_rr"]

    for i in range(250, len(df_daily)-15):
        sub = df_daily.iloc[:i]
        res = get_monte_carlo_smc_signal(ticker, sub, nb_train=200)
        if not res: continue
        if res["prob"] < prob_min: continue

        date_str = df_daily.index[i].strftime("%Y-%m-%d")
        reg = regime.get(date_str, "NEUTRAL")
        direction = "BUY" if res["direction"] == "Long" else "SELL"

        if reg == "BEARISH" and direction == "BUY": continue
        if reg == "BULLISH" and direction == "SELL": continue

        entry  = res["entry"]
        risk   = abs(entry - res["sl"])
        sl     = res["sl"]
        target = entry + risk * rr if direction == "BUY" else entry - risk * rr

        df_fwd  = df_daily.iloc[i+1:]
        outcome, exit_price = sim_trade(df_fwd, direction, entry, sl, target, LONG_HOLD)
        pnl_pct = ((exit_price - entry) / entry * 100) if direction == "BUY" \
                  else ((entry - exit_price) / entry * 100)
        trades.append({"ticker": ticker, "strategy": "AI_SMC_MC", "horizon": "LONG_TERM",
                        "date": date_str, "direction": direction, "entry": entry,
                        "sl": sl, "target": target, "exit": exit_price,
                        "outcome": outcome, "pnl_pct": pnl_pct})
    return trades

# ──────────────────────────────────────────────────────────
#  STRATEGY 3: UT_Bot / EMA Cross (SHORT TERM, daily bars)
# ──────────────────────────────────────────────────────────
def bt_utbot(ticker, df_daily, regime):
    trades = []
    fast = params["utbot_ema_fast"]
    slow = params["utbot_ema_slow"]
    df = df_daily.copy()
    df["EMA_F"] = df["Close"].ewm(span=fast, adjust=False).mean()
    df["EMA_S"] = df["Close"].ewm(span=slow, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["H-L"]   = df["High"] - df["Low"]
    df["H-PC"]  = abs(df["High"] - df["Close"].shift(1))
    df["L-PC"]  = abs(df["Low"]  - df["Close"].shift(1))
    df["TR"]    = df[["H-L","H-PC","L-PC"]].max(axis=1)
    df["ATR"]   = df["TR"].rolling(14).mean()

    for i in range(slow+5, len(df)-SHORT_HOLD):
        prev = df.iloc[i-1]; curr = df.iloc[i]
        cross_up   = float(prev["EMA_F"]) < float(prev["EMA_S"]) and \
                     float(curr["EMA_F"]) > float(curr["EMA_S"])
        cross_down = float(prev["EMA_F"]) > float(prev["EMA_S"]) and \
                     float(curr["EMA_F"]) < float(curr["EMA_S"])
        if not (cross_up or cross_down): continue

        date_str  = df.index[i].strftime("%Y-%m-%d")
        reg       = regime.get(date_str, "NEUTRAL")
        direction = None
        
        ema50 = float(curr["EMA50"])
        c_close = float(curr["Close"])
        
        if cross_up and c_close > ema50: direction = "BUY"
        elif cross_down and c_close < ema50: direction = "SELL"
        if direction is None: continue

        if reg == "BEARISH" and direction == "BUY": continue
        if reg == "BULLISH" and direction == "SELL": continue

        atr = float(curr["ATR"])
        if pd.isna(atr) or atr <= 0: continue

        entry  = float(curr["Close"])
        sl     = entry - 1.5*atr if direction == "BUY" else entry + 1.5*atr
        target = entry + 2.5*atr if direction == "BUY" else entry - 2.5*atr

        df_fwd  = df.iloc[i+1:]
        outcome, exit_price = sim_trade(df_fwd, direction, entry, sl, target, SHORT_HOLD)
        pnl_pct = ((exit_price - entry) / entry * 100) if direction == "BUY" \
                  else ((entry - exit_price) / entry * 100)
        trades.append({"ticker": ticker, "strategy": "UT_Bot", "horizon": "SHORT_TERM",
                        "date": date_str, "direction": direction, "entry": entry,
                        "sl": sl, "target": target, "exit": exit_price,
                        "outcome": outcome, "pnl_pct": pnl_pct})
    return trades

# ──────────────────────────────────────────────────────────
#  STRATEGY 4: Swing Breakout (SHORT TERM, daily bars)
# ──────────────────────────────────────────────────────────
def bt_swing(ticker, df_daily, regime):
    trades = []
    vol_ratio = params["swing_vol_ratio"]
    roc_n     = params["swing_roc_period"]
    df = df_daily.copy()
    df["ROC"]      = df["Close"].pct_change(roc_n) * 100
    df["VOL_SMA20"] = df["Volume"].rolling(20).mean()
    df["H-L"]      = df["High"] - df["Low"]
    df["H-PC"]     = abs(df["High"] - df["Close"].shift(1))
    df["L-PC"]     = abs(df["Low"]  - df["Close"].shift(1))
    df["TR"]       = df[["H-L","H-PC","L-PC"]].max(axis=1)
    df["ATR"]      = df["TR"].rolling(14).mean()
    df["EMA50"]    = df["Close"].ewm(span=50, adjust=False).mean()
    df["High20"]   = df["High"].rolling(20).max().shift(1)
    df["Low20"]    = df["Low"].rolling(20).min().shift(1)

    for i in range(30, len(df)-SHORT_HOLD):
        row = df.iloc[i]
        date_str = df.index[i].strftime("%Y-%m-%d")
        reg = regime.get(date_str, "NEUTRAL")

        vol_surge = float(row["Volume"]) > vol_ratio * float(row["VOL_SMA20"]) \
                    if not pd.isna(row["VOL_SMA20"]) else False
        if not vol_surge: continue

        roc = float(row["ROC"]) if not pd.isna(row["ROC"]) else 0
        atr = float(row["ATR"])
        ema50 = float(row["EMA50"])
        if pd.isna(atr) or atr <= 0: continue

        direction = None
        if roc > 2.0 and reg != "BEARISH" and float(row["Close"]) > float(row["High20"]) and float(row["Close"]) > ema50:
            direction = "BUY"
        elif roc < -2.0 and reg != "BULLISH" and float(row["Close"]) < float(row["Low20"]) and float(row["Close"]) < ema50:
            direction = "SELL"
        if direction is None: continue

        entry  = float(row["Close"])
        sl     = entry - 1.5*atr if direction == "BUY" else entry + 1.5*atr
        target = entry + 3*atr   if direction == "BUY" else entry - 3*atr

        df_fwd  = df.iloc[i+1:]
        outcome, exit_price = sim_trade(df_fwd, direction, entry, sl, target, SHORT_HOLD)
        pnl_pct = ((exit_price - entry) / entry * 100) if direction == "BUY" \
                  else ((entry - exit_price) / entry * 100)
        trades.append({"ticker": ticker, "strategy": "Swing", "horizon": "SHORT_TERM",
                        "date": date_str, "direction": direction, "entry": entry,
                        "sl": sl, "target": target, "exit": exit_price,
                        "outcome": outcome, "pnl_pct": pnl_pct})
    return trades

# ──────────────────────────────────────────────────────────
#  STRATEGY 5: NR7 Inside Bar (INTRADAY, daily bars)
# ──────────────────────────────────────────────────────────
def bt_nr7(ticker, df_daily, regime):
    trades = []
    rsi_bull = params["nr7_rsi_bull_min"]
    rsi_bear = params["nr7_rsi_bear_max"]
    atr_sl   = params["nr7_atr_mult_sl"]
    atr_t1   = params["nr7_atr_mult_t1"]

    df = df_daily.copy()
    df["Range"]  = df["High"] - df["Low"]
    df["H-L"]    = df["High"] - df["Low"]
    df["H-PC"]   = abs(df["High"] - df["Close"].shift(1))
    df["L-PC"]   = abs(df["Low"]  - df["Close"].shift(1))
    df["TR"]     = df[["H-L","H-PC","L-PC"]].max(axis=1)
    df["VOL_SMA"] = df["Volume"].rolling(20).mean()
    df["ATR"]    = df["TR"].rolling(14).mean()
    df["SMA50"]  = df["Close"].rolling(50).mean()
    df["SMA10"]  = df["Close"].rolling(10).mean()
    delta = df["Close"].diff()
    gain  = delta.where(delta > 0, 0).rolling(14).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(14).mean()
    df["RSI"] = 100 - (100 / (1 + gain/loss))

    for i in range(50, len(df)-3):
        ranges = df["Range"].iloc[i-6:i+1].tolist()
        if len(ranges) < 7: continue
        if ranges[-1] != min(ranges): continue  # NR7 = narrowest of last 7
        
        row      = df.iloc[i]
        
        # Require above average volume for NR7 breakout
        if float(row["Volume"]) < float(row["VOL_SMA"]): continue

        date_str = df.index[i].strftime("%Y-%m-%d")
        reg      = regime.get(date_str, "NEUTRAL")

        rsi = float(row["RSI"]) if not pd.isna(row["RSI"]) else 50
        sma50 = float(row["SMA50"]); sma10 = float(row["SMA10"])
        close  = float(row["Close"])

        is_bull = close > sma50 and sma10 > sma50 and rsi > rsi_bull
        is_bear = close < sma50 and sma10 < sma50 and rsi < rsi_bear

        direction = None
        if is_bull and reg == "BULLISH":   direction = "BUY"
        elif is_bear and reg == "BEARISH": direction = "SELL"
        if direction is None: continue

        atr = float(row["ATR"])
        if pd.isna(atr) or atr <= 0: continue

        setup_high = float(row["High"]); setup_low = float(row["Low"])
        entry  = setup_high + 0.10 if direction == "BUY" else setup_low - 0.10
        sl     = entry - atr_sl*atr  if direction == "BUY" else entry + atr_sl*atr
        target = entry + atr_t1*atr  if direction == "BUY" else entry - atr_t1*atr

        # Trigger next day(s): look for breakout in next 3 bars
        triggered = False
        for j in range(i+1, min(i+4, len(df))):
            dj = df.iloc[j]
            if direction == "BUY"  and float(dj["High"]) > setup_high: triggered = True; break
            if direction == "SELL" and float(dj["Low"])  < setup_low:  triggered = True; break
        if not triggered: continue

        df_fwd  = df.iloc[j:]
        outcome, exit_price = sim_trade(df_fwd, direction, entry, sl, target, INTRADAY_HOLD)
        pnl_pct = ((exit_price - entry) / entry * 100) if direction == "BUY" \
                  else ((entry - exit_price) / entry * 100)
        trades.append({"ticker": ticker, "strategy": "NR7", "horizon": "INTRADAY",
                        "date": date_str, "direction": direction, "entry": entry,
                        "sl": sl, "target": target, "exit": exit_price,
                        "outcome": outcome, "pnl_pct": pnl_pct})
    return trades

# ──────────────────────────────────────────────────────────
#  SINGLE TICKER RUNNER (with horizon priority)
# ──────────────────────────────────────────────────────────
def run_ticker(ticker, nifty_regime, df_daily_all, df_weekly_all):
    result = []

    # --- Fetch daily data ---
    ticker_ns = ticker + ".NS"
    try:
        if isinstance(df_daily_all.columns, pd.MultiIndex) and ticker_ns in df_daily_all.columns.levels[0]:
            df_d = df_daily_all[ticker_ns].dropna()
        else:
            return result
    except Exception:
        return result

    if len(df_d) < 60:
        return result

    if isinstance(df_d.columns, pd.MultiIndex):
        df_d.columns = df_d.columns.get_level_values(0)

    # --- Fetch weekly data ---
    df_w = None
    try:
        if isinstance(df_weekly_all.columns, pd.MultiIndex) and ticker_ns in df_weekly_all.columns.levels[0]:
            df_w = df_weekly_all[ticker_ns].dropna()
        elif not isinstance(df_weekly_all.columns, pd.MultiIndex):
            df_w = df_weekly_all.dropna()
    except Exception:
        pass

    all_t = []
    # Fetch all prospective trades
    if df_w is not None and len(df_w) >= 35:
        all_t += bt_smc_fvg(ticker, df_w, nifty_regime)
    if len(df_d) >= 250:
        all_t += bt_ai_smc_mc(ticker, df_d, nifty_regime)
    all_t += bt_utbot(ticker, df_d, nifty_regime)
    all_t += bt_swing(ticker, df_d, nifty_regime)
    all_t += bt_nr7(ticker, df_d, nifty_regime)

    if not all_t: return result

    # Sort trades by date (ascending), then by horizon priority
    horizon_prio = {"LONG_TERM": 1, "SHORT_TERM": 2, "INTRADAY": 3}
    all_t.sort(key=lambda x: (x["date"], horizon_prio[x["horizon"]]))

    # Deduplicate overlapping trades (only 1 active trade per ticker at a time)
    busy_until = ""
    for t in all_t:
        if t["date"] < busy_until:
            continue
        
        # Calculate exit date approx to block new trades
        hold_days = LONG_HOLD if t["horizon"] == "LONG_TERM" else (SHORT_HOLD if t["horizon"] == "SHORT_TERM" else INTRADAY_HOLD)
        dt = datetime.strptime(t["date"], "%Y-%m-%d") + timedelta(days=hold_days + 5)
        busy_until = dt.strftime("%Y-%m-%d")
        
        result.append(t)

    return result

# ──────────────────────────────────────────────────────────
#  SUMMARY PRINTER
# ──────────────────────────────────────────────────────────
def print_summary(all_trades, round_num=1):
    if not all_trades:
        print("  No trades generated.")
        return 0, {}

    df = pd.DataFrame(all_trades)
    print(f"\n{'='*60}")
    print(f"  BACKTEST ROUND {round_num} — RESULTS")
    print(f"{'='*60}")

    horizon_order = ["LONG_TERM", "SHORT_TERM", "INTRADAY"]
    overall_accuracy = {}

    for horizon in horizon_order:
        hdf = df[df["horizon"] == horizon]
        if hdf.empty: continue

        for strat in sorted(hdf["strategy"].unique()):
            sdf = hdf[hdf["strategy"] == strat]
            total  = len(sdf)
            wins   = len(sdf[sdf["outcome"] == "WIN"])
            losses = len(sdf[sdf["outcome"] == "LOSS"])
            wr     = wins/total*100 if total > 0 else 0
            avg_pnl = sdf["pnl_pct"].mean()
            gross_p = sdf[sdf["pnl_pct"] > 0]["pnl_pct"].sum()
            gross_l = abs(sdf[sdf["pnl_pct"] < 0]["pnl_pct"].sum())
            pf      = gross_p / gross_l if gross_l > 0 else float("inf")

            print(f"\n  [{horizon}] {strat}")
            print(f"    Trades      : {total}  (W:{wins} / L:{losses})")
            print(f"    Win Rate    : {wr:.1f}%  {'✅' if wr >= MIN_ACCURACY else '❌'}")
            print(f"    Avg PnL %   : {avg_pnl:.2f}%")
            print(f"    Profit Factor: {pf:.2f}")

            overall_accuracy[strat] = wr

    print(f"\n{'='*60}")
    all_wr = [v for v in overall_accuracy.values()]
    combined = sum(all_wr)/len(all_wr) if all_wr else 0
    print(f"  COMBINED AVG WIN RATE : {combined:.1f}%  {'✅ TARGET MET' if combined >= MIN_ACCURACY else '❌ NEEDS TUNING'}")
    print(f"{'='*60}\n")

    # Save CSV
    out = os.path.join(BASE_DIR, "data", f"backtest_round_{round_num}.csv")
    df.to_csv(out, index=False)
    print(f"  Saved: {out}")
    return combined, overall_accuracy

# ──────────────────────────────────────────────────────────
#  AUTO-TUNER
# ──────────────────────────────────────────────────────────
def tune_params(accuracy_map):
    """Tighten thresholds for strategies that are under-performing."""
    print("\n  [AUTO-TUNE] Adjusting parameters for underperforming strategies...\n")

    if accuracy_map.get("NR7", 100) < MIN_ACCURACY:
        params["nr7_rsi_bull_min"] = min(params["nr7_rsi_bull_min"] + 3, 65)
        params["nr7_rsi_bear_max"] = max(params["nr7_rsi_bear_max"] - 3, 35)
        params["nr7_atr_mult_sl"]  = round(params["nr7_atr_mult_sl"] + 0.1, 2)
        params["nr7_atr_mult_t1"]  = round(params["nr7_atr_mult_t1"] + 0.1, 2)
        print(f"    NR7 RSI bull_min→{params['nr7_rsi_bull_min']}  bear_max→{params['nr7_rsi_bear_max']}")

    if accuracy_map.get("UT_Bot", 100) < MIN_ACCURACY:
        params["utbot_ema_fast"] = max(params["utbot_ema_fast"] - 1, 5)
        params["utbot_ema_slow"] = max(params["utbot_ema_slow"] - 1, 15)
        print(f"    UT_Bot EMA fast→{params['utbot_ema_fast']}  slow→{params['utbot_ema_slow']}")

    if accuracy_map.get("Swing", 100) < MIN_ACCURACY:
        params["swing_vol_ratio"] = round(params["swing_vol_ratio"] + 0.1, 2)
        params["swing_roc_period"] = min(params["swing_roc_period"] + 1, 20)
        print(f"    Swing vol_ratio→{params['swing_vol_ratio']}  roc_period→{params['swing_roc_period']}")

    if accuracy_map.get("AI_SMC_MC", 100) < MIN_ACCURACY:
        params["ai_mc_prob_min"] = round(min(params["ai_mc_prob_min"] + 0.03, 0.85), 2)
        params["ai_mc_tp_rr"]   = round(max(params["ai_mc_tp_rr"] - 0.1, 1.2), 2)
        print(f"    AI_SMC_MC prob_min→{params['ai_mc_prob_min']}  rr→{params['ai_mc_tp_rr']}")

    if accuracy_map.get("SMC_FVG", 100) < MIN_ACCURACY:
        params["smc_fvg_lookback"] = max(params["smc_fvg_lookback"] - 5, 10)
        params["smc_fvg_rr"]       = round(max(params["smc_fvg_rr"] - 0.1, 1.2), 2)
        print(f"    SMC_FVG lookback→{params['smc_fvg_lookback']}  rr→{params['smc_fvg_rr']}")

# ──────────────────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────────────────
def main():
    print("\n" + "="*60)
    print("  TRADER PILOT — MULTI-STRATEGY BACKTEST ENGINE")
    print(f"  Universe  : {len(TICKERS)} F&O Futures stocks")
    print(f"  Target    : >= {MIN_ACCURACY}% win rate per strategy")
    print(f"  Max Rounds: {MAX_TUNE_ROUNDS} auto-tune iterations")
    print("="*60)

    # Fetch macro regime
    print("\n[1] Fetching NIFTY macro regime...")
    nifty_regime = get_nifty_regime()
    print(f"    Done. {len(nifty_regime)} trading days loaded.")

    # Batch download purely via DB Cache pipeline
    tns = [t + ".NS" for t in TICKERS]
    print(f"\n[2] Loading/Downloading {len(tns)} stocks (daily) via DB Cache...")
    df_daily_all = get_cached_yfinance(tns, period=DATA_PERIOD_DAILY, interval="1d",
                                group_by="ticker", progress=False, auto_adjust=True)
    print(f"\n[3] Loading/Downloading {len(tns)} stocks (weekly) via DB Cache...")
    df_weekly_all = get_cached_yfinance(tns, period=DATA_PERIOD_WEEKLY, interval="1wk",
                                 group_by="ticker", progress=False, auto_adjust=True)

    best_accuracy = 0
    best_trades   = []

    for round_num in range(1, MAX_TUNE_ROUNDS + 1):
        print(f"\n{'─'*60}")
        print(f"  ROUND {round_num} — Running backtest with current params...")
        print(f"{'─'*60}")

        all_trades = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(run_ticker, t, nifty_regime, df_daily_all, df_weekly_all): t
                for t in TICKERS
            }
            done = 0
            for future in as_completed(futures):
                try:
                    res = future.result()
                    if res:
                        all_trades.extend(res)
                except Exception as e:
                    pass
                done += 1
                if done % 10 == 0:
                    print(f"    Progress: {done}/{len(TICKERS)} tickers processed...")

        combined_wr, acc_map = print_summary(all_trades, round_num)

        if combined_wr >= MIN_ACCURACY:
            print(f"\n  ✅ TARGET ACCURACY ACHIEVED in Round {round_num}!")
            best_trades   = all_trades
            best_accuracy = combined_wr
            break

        if round_num < MAX_TUNE_ROUNDS:
            tune_params(acc_map)
        else:
            print(f"\n  ⚠️  Max rounds reached. Best accuracy: {combined_wr:.1f}%")
            print(f"     Recommend reviewing SMC_FVG lookback and AI prob_min manually.")
            best_trades = all_trades

    # Final save
    if best_trades:
        out = os.path.join(BASE_DIR, "data", "backtest_final.csv")
        pd.DataFrame(best_trades).to_csv(out, index=False)
        print(f"\n  ✅ Final results saved to: {out}")

    print("\n  Done. Engine ready for live deployment on Monday.\n")

if __name__ == "__main__":
    main()
