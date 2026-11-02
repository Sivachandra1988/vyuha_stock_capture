import os
import json
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from db_cache import get_cached_yfinance
from scanner import core
from scanner import trades
from scanner.smc_ai import get_monte_carlo_smc_signal

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")

WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.txt")
FO_TICKERS_FILE = os.path.join(CONFIG_DIR, "fo_tickers.json")

def load_fo_tickers():
    if os.path.exists(FO_TICKERS_FILE):
        try:
            with open(FO_TICKERS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {FO_TICKERS_FILE}: {e}")
    return []

def check_nr7_for_dataframe(ticker, df):
    """Processes a downloaded DataFrame for a single ticker to check for NR7 setup."""
    try:
        df = df.copy()
        df['Range'] = df['High'] - df['Low']
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Inside Bar (Strict inside bar)
        inside_bar = (latest['High'] < prev['High']) and (latest['Low'] > prev['Low'])
        
        # NR7 Setup
        ranges = df['Range'].tail(7).tolist()
        if len(ranges) < 7:
            return None
        is_nr7 = ranges[-1] == min(ranges)
        
        if inside_bar and is_nr7:
            df['SMA_10'] = df['Close'].rolling(10).mean()
            df['SMA_50'] = df['Close'].rolling(50).mean()
            df['SMA_200'] = df['Close'].rolling(200).mean()
            
            c_close = latest['Close']
            c_sma10 = df['SMA_10'].iloc[-1]
            c_sma50 = df['SMA_50'].iloc[-1]
            c_sma200 = df['SMA_200'].iloc[-1]
            
            # Perfect SMA Validation
            is_bullish = (c_close > c_sma50) and (c_sma10 > c_sma50) and (c_sma50 > c_sma200)
            is_bearish = (c_close < c_sma50) and (c_sma10 < c_sma50) and (c_sma50 < c_sma200)
            
            # Percent change
            prev_close = float(prev['Close'])
            pct_change = abs((float(c_close) - prev_close) / prev_close) * 100.0 if prev_close > 0 else 0.0
            
            if is_bullish:
                entry = latest['High'] + 0.10
                sl = latest['Low'] - 0.10
                risk = entry - sl
                return ticker, "NR7", "B/O Call", pct_change, entry, sl, entry + (2*risk), entry + (3*risk)
            elif is_bearish:
                entry = latest['Low'] - 0.10
                sl = latest['High'] + 0.10
                risk = sl - entry
                return ticker, "NR7", "Short", pct_change, entry, sl, entry - (2*risk), entry - (3*risk)
    except Exception:
        pass
    return None

def check_confluence_for_dataframe(ticker, df):
    """Processes a downloaded 15m DataFrame for Confluence breakout/breakdown (5.1/5.2)."""
    try:
        df = df.copy()
        unique_dates = sorted(list(set(df.index.date)))
        if len(unique_dates) < 2:
            return None
            
        yesterday_date = unique_dates[-2]
        df_yesterday = df[df.index.date == yesterday_date]
        if df_yesterday.empty:
            return None
            
        yesterday_high = float(df_yesterday['High'].max())
        yesterday_low = float(df_yesterday['Low'].min())
        yesterday_close = float(df_yesterday['Close'].iloc[-1])
        
        pp = (yesterday_high + yesterday_low + yesterday_close) / 3.0
        r1 = (2.0 * pp) - yesterday_low
        s1 = (2.0 * pp) - yesterday_high
        
        df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
        
        df['Typical_Price'] = (df['High'] + df['Low'] + df['Close']) / 3.0
        df['TP_Vol'] = df['Typical_Price'] * df['Volume']
        
        dates = df.index.date
        df['Cum_TP_Vol'] = df.groupby(dates)['TP_Vol'].transform('cumsum')
        df['Cum_Vol'] = df.groupby(dates)['Volume'].transform('cumsum')
        df['VWAP'] = df['Cum_TP_Vol'] / df['Cum_Vol']
        
        latest = df.iloc[-1]
        prev_candle = df.iloc[-2]
        
        c_close = float(latest['Close'])
        c_vwap = float(latest['VWAP'])
        c_ema20 = float(latest['EMA_20'])
        p_ema20 = float(prev_candle['EMA_20'])
        c_ema200 = float(latest['EMA_200'])
        
        is_bullish = (
            (c_close > yesterday_high) and
            (c_close > c_vwap) and
            (c_close > c_ema20) and
            (c_ema20 > p_ema20) and
            (c_close > c_ema200) and
            (c_ema20 > c_ema200) and
            (c_close > pp) and
            (c_close > r1)
        )
        
        is_bearish = (
            (c_close < yesterday_low) and
            (c_close < c_vwap) and
            (c_close < c_ema20) and
            (c_ema20 < p_ema20) and
            (c_close < c_ema200) and
            (c_ema20 < c_ema200) and
            (c_close < pp) and
            (c_close < s1)
        )
        
        # Percent change since yesterday's close
        pct_change = abs((c_close - yesterday_close) / yesterday_close) * 100.0 if yesterday_close > 0 else 0.0
        
        # Calculate ATR for SL and Targets
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
        atr = df['TR'].rolling(14).mean().iloc[-1]
        
        if is_bullish:
            entry = c_close
            sl = yesterday_low - atr
            risk = entry - sl
            if risk <= 0: risk = entry * 0.01
            return ticker, "Swing", "Long", pct_change, entry, sl, entry + (2*risk), entry + (3*risk)
        elif is_bearish:
            entry = c_close
            sl = yesterday_high + atr
            risk = sl - entry
            if risk <= 0: risk = entry * 0.01
            return ticker, "Swing", "Short", pct_change, entry, sl, entry - (2*risk), entry - (3*risk)
    except Exception:
        pass
    return None

def check_smc_longterm(ticker, df):
    """Processes DataFrame (weekly/daily) for SMC Patterns (FVG) for Long Term Investments."""
    try:
        df = df.copy()
        if len(df) < 35:
            return None
            
        latest = df.iloc[-1]
        c_close = float(latest['Close'])
        c_low = float(latest['Low'])
        c_high = float(latest['High'])
        
        bullish_fvg = None
        bearish_fvg = None
        
        # Lookback last 18 bars per Auto-Tuner threshold optimization to find unfilled FVGs
        for i in range(len(df)-18, len(df)-1):
            if i < 1: continue
            c1 = df.iloc[i-1]
            c2 = df.iloc[i]
            c3 = df.iloc[i+1]
            
            # Check for bullish FVG created (c3 Low > c1 High)
            if float(c3['Low']) > float(c1['High']) and float(c2['Close']) > float(c2['Open']):
                fvg_bot = float(c1['High'])
                fvg_top = float(c3['Low'])
                
                # Verify if it was completely filled by any candle after i+1
                filled = False
                for j in range(i+2, len(df)-1):
                    if float(df.iloc[j]['Low']) < fvg_bot:
                        filled = True
                        break
                
                if not filled:
                    bullish_fvg = (fvg_bot, fvg_top, i)
            
            # Check for bearish FVG created (c3 High < c1 Low)
            if float(c3['High']) < float(c1['Low']) and float(c2['Open']) > float(c2['Close']):
                fvg_top = float(c1['Low'])
                fvg_bot = float(c3['High'])
                
                # Verify if it was completely filled by any candle after i+1
                filled = False
                for j in range(i+2, len(df)-1):
                    if float(df.iloc[j]['High']) > fvg_top:
                        filled = True
                        break
                
                if not filled:
                    bearish_fvg = (fvg_bot, fvg_top, i)

        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
        atr_val = df['TR'].rolling(14).mean().iloc[-1]
        
        df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['SMA50'] = df['Close'].rolling(50).mean()
        df['EMA9']  = df['Close'].ewm(span=9, adjust=False).mean()

        ema20 = float(df['EMA20'].iloc[-1])
        sma50 = float(df['SMA50'].iloc[-1])
        ema9  = float(df['EMA9'].iloc[-1])
        ema9_prev = float(df['EMA9'].iloc[-4])  # 3 days ago slope proxy
        
        # Check mitigation by latest candle (price enters the zone)
        is_bullish_mitigation = False
        is_bearish_mitigation = False
        
        if bullish_fvg and c_low <= bullish_fvg[1] and c_close > bullish_fvg[0]:
            if c_close > ema20 and c_close > sma50:
                is_bullish_mitigation = True
            
        if bearish_fvg and c_high >= bearish_fvg[0] and c_close < bearish_fvg[1]:
            if c_close < ema20 and c_close < sma50:
                is_bearish_mitigation = True
            
        pct_change = abs((c_close - float(df.iloc[-2]['Close'])) / float(df.iloc[-2]['Close'])) * 100.0 if float(df.iloc[-2]['Close']) > 0 else 0.0
        
        if is_bullish_mitigation:
            entry = c_close
            sl = bullish_fvg[0] - atr_val 
            risk = entry - sl
            if risk <= 0: risk = entry * 0.05
            return ticker, "SMC_FVG", "Long", pct_change, entry, sl, entry + (1.8*risk), entry + (3*risk)
            
        elif is_bearish_mitigation:
            entry = c_close
            sl = bearish_fvg[1] + atr_val
            risk = sl - entry
            if risk <= 0: risk = entry * 0.05
            return ticker, "SMC_FVG", "Short", pct_change, entry, sl, entry - (1.8*risk), entry - (3*risk)

    except Exception:
        pass
    return None


def run_eod_scan(send_telegram_fn, market_trend="NEUTRAL"):
    """Runs a bulk batch scan on the F&O universe at the end of the day to build tomorrow's watchlist."""
    tickers = core.get_dynamic_tickers()
    if not tickers:
        print("F&O tickers list is empty.")
        return
        
    print(f"\n--- [INFO] Running Bulk EOD Batch Scanner @ {datetime.now().strftime('%H:%M:%S')} (Trend: {market_trend}) ---")
    
    if market_trend == "CHOPPY":
        print("[INFO] Market is currently CHOPPY. Running EOD scan anyway, but setups may be volatile.")
    ticker_ns_list = [t + ".NS" for t in tickers]
    
    # 1. Download 1y data for all F&O tickers in a single thread-safe batch request
    print(f"Downloading 1-year historical daily bars for {len(tickers)} symbols...")
    try:
        df_all = get_cached_yfinance(ticker_ns_list, period="1y", group_by="ticker", progress=False, auto_adjust=True)
    except Exception as e:
        print(f"Error during EOD batch download: {e}")
        return
        
    nr7_candidates = []
    utbot_candidates = []
    for ticker in tickers:
        ticker_ns = ticker + ".NS"
        try:
            if ticker_ns in df_all.columns.levels[0]:
                df_ticker = df_all[ticker_ns].dropna()
                if len(df_ticker) >= 200:
                    res = check_nr7_for_dataframe(ticker, df_ticker)
                    if res:
                        direction = res[2]
                        if market_trend == "BEARISH" and direction != "Short":
                            pass
                        elif market_trend == "BULLISH" and direction != "B/O Call":
                            pass
                        else:
                            proj = core.predict_future_trend(df_ticker)
                            latest = df_ticker.iloc[-1]
                            vol_mean = df_ticker['Volume'].rolling(20).mean().iloc[-1]
                            vol_ratio = latest['Volume'] / vol_mean if vol_mean > 0 else 0
                            atr_pct = ((latest['High'] - latest['Low']) / latest['Close']) * 100
                            if 'ATR' in df_ticker.columns: atr_pct = (latest['ATR'] / latest['Close']) * 100
                            is_intra = (atr_pct > 3.5 or vol_ratio > 2.0)
                            nr7_candidates.append((*res, proj, is_intra))
                            
                if len(df_ticker) >= 20:
                    ut_res = core.check_utbot_for_dataframe(ticker, df_ticker)
                    if ut_res:
                        direction = ut_res[2]
                        if market_trend == "BEARISH" and direction != "Short":
                            pass
                        elif market_trend == "BULLISH" and direction != "Long":
                            pass
                        else:
                            proj = core.predict_future_trend(df_ticker)
                            latest = df_ticker.iloc[-1]
                            vol_mean = df_ticker['Volume'].rolling(20).mean().iloc[-1]
                            vol_ratio = latest['Volume'] / vol_mean if vol_mean > 0 else 0
                            atr_pct = ((latest['High'] - latest['Low']) / latest['Close']) * 100
                            if 'ATR' in df_ticker.columns: atr_pct = (latest['ATR'] / latest['Close']) * 100
                            is_intra = (atr_pct > 3.5 or vol_ratio > 2.0)
                            utbot_candidates.append((*ut_res, proj, is_intra))
        except Exception:
            pass
            
    print(f"NR7 Inside Bar scan complete. Found {len(nr7_candidates)} candidates matching trend.")
    print(f"UT Bot Swing scan complete. Found {len(utbot_candidates)} candidates matching trend.")
    
    swing_candidates = []
    # 2. FALLBACK / Secondary: Run Confluence Scan
    print(f"Downloading 15-minute bars for {len(tickers)} symbols...")
    try:
        df_all_15m = get_cached_yfinance(ticker_ns_list, period="5d", interval="15m", group_by="ticker", progress=False, auto_adjust=True)
    except Exception as e:
        print(f"Error during Confluence batch download: {e}")
        return
        
    for ticker in tickers:
        ticker_ns = ticker + ".NS"
        try:
            if ticker_ns in df_all_15m.columns.levels[0]:
                df_ticker = df_all_15m[ticker_ns].dropna()
                if len(df_ticker) >= 50:
                    res = check_confluence_for_dataframe(ticker, df_ticker)
                    if res:
                        direction = res[2]
                        if market_trend == "BULLISH" and direction != "Long":
                            continue
                        elif market_trend == "BEARISH" and direction != "Short":
                            continue
                        proj = core.predict_future_trend(df_ticker)
                        latest = df_ticker.iloc[-1]
                        vol_mean = df_ticker['Volume'].rolling(20).mean().iloc[-1]
                        vol_ratio = latest['Volume'] / vol_mean if vol_mean > 0 else 0
                        atr_pct = ((latest['High'] - latest['Low']) / latest['Close']) * 100
                        if 'ATR' in df_ticker.columns: atr_pct = (latest['ATR'] / latest['Close']) * 100
                        is_intra = (atr_pct > 3.5 or vol_ratio > 2.0)
                        swing_candidates.append((*res, proj, is_intra))
        except Exception:
            pass
    print(f"Confluence Breakout/Breakdown scan complete. Found {len(swing_candidates)} candidates matching trend.")
    
    # 3. SMC LONG TERM STOCKS SCAN (Weekly data)
    print(f"Downloading 2-year weekly bars for {len(tickers)} symbols...")
    try:
        df_all_wk = get_cached_yfinance(ticker_ns_list, period="2y", interval="1wk", group_by="ticker", progress=False, auto_adjust=True)
    except Exception as e:
        print(f"Error during SMC Weekly batch download: {e}")
        df_all_wk = pd.DataFrame()
        
    smc_candidates = []
    if not df_all_wk.empty:
        for ticker in tickers:
            ticker_ns = ticker + ".NS"
            try:
                if (isinstance(df_all_wk.columns, pd.MultiIndex) and ticker_ns in df_all_wk.columns.levels[0]) or (not isinstance(df_all_wk.columns, pd.MultiIndex) and ticker_ns in df_all_wk.columns):
                    df_ticker = df_all_wk[ticker_ns].dropna() if isinstance(df_all_wk.columns, pd.MultiIndex) else df_all_wk.dropna()
                    if len(df_ticker) >= 20:
                        res = check_smc_longterm(ticker, df_ticker)
                        if res:
                            direction = res[2]
                            if market_trend == "BULLISH" and direction != "Long":
                                continue
                            elif market_trend == "BEARISH" and direction != "Short":
                                continue
                            
                            proj = core.predict_future_trend(df_ticker)
                            is_intra = False # Long term is never intraday
                            
                            smc_candidates.append((*res, proj, is_intra))
            except Exception:
                pass
    print(f"SMC Long Term scan complete. Found {len(smc_candidates)} candidates.")
    
    print(f"Running Monte Carlo AI scan...")
    ai_mc_candidates = []
    for ticker in tickers:
        ticker_ns = ticker + ".NS"
        try:
            if (isinstance(df_all.columns, pd.MultiIndex) and ticker_ns in df_all.columns.levels[0]) or (not isinstance(df_all.columns, pd.MultiIndex) and ticker_ns in df_all.columns):
                df_ticker = df_all[ticker_ns].dropna() if isinstance(df_all.columns, pd.MultiIndex) else df_all.dropna()
                if len(df_ticker) >= 200:
                    res = get_monte_carlo_smc_signal(ticker, df_ticker)
                    if res:
                        direction = res["direction"]
                        if market_trend == "BULLISH" and direction != "Long":
                            continue
                        elif market_trend == "BEARISH" and direction != "Short":
                            continue
                        
                        proj = core.predict_future_trend(df_ticker)
                        ai_mc_candidates.append((ticker, "AI_SMC_MC", direction, res["pct_change"], res["entry"], res["sl"], res["target1"], res["target2"], proj, False, res["prob"]))
        except Exception:
            pass
    print(f"Monte Carlo AI scan complete. Found {len(ai_mc_candidates)} candidates.")

    # Sort and filter top 5 for each strategy
    nr7_candidates = sorted(nr7_candidates, key=lambda x: x[3], reverse=True)[:5]
    swing_candidates = sorted(swing_candidates, key=lambda x: x[3], reverse=True)[:5]
    utbot_candidates = sorted(utbot_candidates, key=lambda x: x[3], reverse=True)[:5]
    smc_candidates = sorted(smc_candidates, key=lambda x: x[3], reverse=True)[:5]
    
    # Sort AI setups for "Faster Movement" (Velocity calculation: Probability * Current Volatility % Change)
    ai_mc_candidates = sorted(ai_mc_candidates, key=lambda x: x[3] * x[10], reverse=True)[:5]
    
    all_candidates = nr7_candidates + swing_candidates + utbot_candidates + smc_candidates + ai_mc_candidates
    
    if all_candidates:
        import db_storage
        
        existing_lines = db_storage.get_watchlist()
        existing = set([line.split('|')[0].strip() for line in existing_lines])
                
        new_additions = []
        for cand in all_candidates:
            ticker = cand[0]
            strategy = cand[1]
            direction = cand[2]
            pct = cand[3]
            entry = cand[4] if len(cand) > 4 else 0.0
            sl = cand[5] if len(cand) > 5 else 0.0
            t1 = cand[6] if len(cand) > 6 else 0.0
            t2 = cand[7] if len(cand) > 7 else 0.0
            proj = cand[8] if len(cand) > 8 else "PROJ_NONE_0.0"
            is_intra = cand[9] if len(cand) > 9 else False
            
            # Determine trade_type dynamically based on volume & movement
            trade_type = "INTRADAY" if is_intra else "POSITIONAL"
            if strategy in ["SMC_FVG", "AI_SMC_MC"]:
                trade_type = "LONG_TERM"
            
            new_line = f"{ticker} | {strategy} | {direction} | {trade_type} | {entry:.2f} | {proj} | {sl:.2f} | {t1:.2f} | {t2:.2f}"
            
            if ticker not in existing:
                if strategy == "AI_SMC_MC" and getattr(cand, "__len__", lambda: len(cand))() > 10 and cand[10] >= 0.74:
                    new_additions.append(f"🚨 TOP MOVEMENT STOCK: {ticker} ({direction} @ {entry:.2f}) | Win Prob: {cand[10]*100:.1f}%")
                else:
                    new_additions.append(f"{ticker} ({strategy} - {direction} - {pct:.1f}%)")
                
            db_storage.append_to_watchlist(new_line)
            # Queue it up as a PENDING paper trade so it triggers automatically when the entry price is hit
            trades.initiate_paper_trade(ticker, {
                "direction": direction.upper() if direction.upper() in ["BUY", "SELL"] else ("BUY" if direction.upper() == "LONG" else "SELL"),
                "entry": entry,
                "sl": sl,
                "target1": t1,
                "target2": t2,
                "setup_date": datetime.now().strftime("%Y-%m-%d"),
                "trade_type": trade_type
            })
                
        if new_additions:
            msg = (f"🏛️ *VYUHA DAILY WATCHLIST* ({len(new_additions)} Setups)\n"
                   f"Highest calculated movement candidates for tomorrow:\n\n"
                   + "\n".join([f"• {item}" for item in new_additions]))
            send_telegram_fn(msg)
            print(f"[EOD Scan] Identified high movement tickers: {', '.join(new_additions)}")
        else:
            print("[EOD Scan] Candidates identified, but all are already in watchlist.")
    else:
        print("[EOD Scan] No candidates matching NR7 or Confluence criteria found today.")
