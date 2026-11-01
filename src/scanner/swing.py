import os
import json
import yfinance as yf
import pandas as pd
from datetime import datetime
from scanner import trades, core

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")

FO_TICKERS_FILE = os.path.join(CONFIG_DIR, "fo_tickers.json")
ACTIVE_SWING_FILE = os.path.join(DATA_DIR, "active_swing_symbols.json")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.txt")
ALERTS_STATE_FILE = os.path.join(DATA_DIR, "confluence_alerts.json")

alerted_today = set()

def load_alerts_state():
    global alerted_today
    today_str = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(ALERTS_STATE_FILE):
        try:
            with open(ALERTS_STATE_FILE, "r") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    alerted_today = set(data.get("alerts", []))
                    return
        except Exception:
            pass
    alerted_today = set()

def save_alerts_state():
    try:
        with open(ALERTS_STATE_FILE, "w") as f:
            json.dump({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "alerts": list(alerted_today)
            }, f, indent=4)
    except Exception:
        pass

def load_fo_tickers():
    if os.path.exists(FO_TICKERS_FILE):
        try:
            with open(FO_TICKERS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def load_active_swing_symbols():
    if os.path.exists(ACTIVE_SWING_FILE):
        try:
            with open(ACTIVE_SWING_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_active_swing_symbols(symbols):
    try:
        with open(ACTIVE_SWING_FILE, "w") as f:
            json.dump(symbols, f, indent=4)
    except Exception as e:
        print(f"Error saving active swing symbols: {e}")

def filter_active_swing_universe():
    """Scans all F&O stocks and identifies those with >= 1.0% movement since yesterday's close."""
    tickers = core.get_dynamic_tickers()
    if not tickers:
        return []
        
    print(f"[INFO] Filtering F&O universe for active swing stocks (>= 1.0% price change)...")
    ticker_ns_list = [t + ".NS" for t in tickers]
    
    try:
        df_all = yf.download(ticker_ns_list, period="5d", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
    except Exception as e:
        print(f"Error downloading daily prices for swing filter: {e}")
        return []
        
    active_symbols = []
    for ticker in tickers:
        ticker_ns = ticker + ".NS"
        try:
            if ticker_ns in df_all.columns.levels[0]:
                df = df_all[ticker_ns].dropna()
                if len(df) >= 2:
                    yesterday_close = float(df['Close'].iloc[-2])
                    today_close = float(df['Close'].iloc[-1])
                    if yesterday_close > 0:
                        pct_change = ((today_close - yesterday_close) / yesterday_close) * 100
                        if abs(pct_change) >= 1.0:
                            active_symbols.append(ticker)
        except Exception:
            pass
            
    save_active_swing_symbols(active_symbols)
    print(f"[INFO] Daily active swing universe rebuilt: Cached {len(active_symbols)} moving stocks.")
    return active_symbols

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
        
        # 5.1 Bullish Confluence Breakout (Long Setup)
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
        
        # 5.2 Bearish Confluence Breakdown (Short Setup)
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
        
        pct_change = abs((c_close - yesterday_close) / yesterday_close) * 100.0 if yesterday_close > 0 else 0.0

        if is_bullish:
            entry = c_close
            sl = yesterday_low
            risk = entry - sl
            if risk <= 0:
                risk = entry * 0.01
            target1 = entry + (2 * risk)
            target2 = entry + (3 * risk)
            
            return {
                "ticker": ticker,
                "direction": "BUY",
                "entry": entry,
                "sl": sl,
                "target1": target1,
                "target2": target2,
                "status": "CONFLUENCE_BREAKOUT",
                "setup_date": datetime.now().strftime("%Y-%m-%d"),
                "pct_change": pct_change,
                "msg": (f"[INFO] INTRADAY CONFLUENCE BREAKOUT (Long Entry)\n"
                        f"Stock: #{ticker}\n"
                        f"Live Entry Price: `{entry:.2f}`\n"
                        f"Stop Loss: `{sl:.2f}`\n"
                        f"Target 1 (1:2): `{target1:.2f}`\n"
                        f"Target 2 (1:3): `{target2:.2f}`")
            }
        elif is_bearish:
            entry = c_close
            sl = yesterday_high
            risk = sl - entry
            if risk <= 0:
                risk = entry * 0.01
            target1 = entry - (2 * risk)
            target2 = entry - (3 * risk)
            
            return {
                "ticker": ticker,
                "direction": "SELL",
                "entry": entry,
                "sl": sl,
                "target1": target1,
                "target2": target2,
                "status": "CONFLUENCE_BREAKDOWN",
                "setup_date": datetime.now().strftime("%Y-%m-%d"),
                "pct_change": pct_change,
                "msg": (f"[INFO] INTRADAY CONFLUENCE BREAKDOWN (Short Entry)\n"
                        f"Stock: #{ticker}\n"
                        f"Live Entry Price: `{entry:.2f}`\n"
                        f"Stop Loss: `{sl:.2f}`\n"
                        f"Target 1 (1:2): `{target1:.2f}`\n"
                        f"Target 2 (1:3): `{target2:.2f}`")
            }
    except Exception:
        pass
    return None

def execute_swing_scan(send_telegram_fn, force_full=False, market_trend="NEUTRAL"):
    """Executes the confluence scan cycle on the cached active swing stocks."""
    load_alerts_state()
    
    # Rebuild active swing universe if cache doesn't exist or if forced (10:15 initial execution)
    if force_full or not os.path.exists(ACTIVE_SWING_FILE):
        tickers = filter_active_swing_universe()
    else:
        tickers = load_active_swing_symbols()
        
    if not tickers:
        print("[INFO] Active swing stocks list is empty.")
        return
        
    print(f"\n--- [INFO] Running Scheduled Swing Scan @ {datetime.now().strftime('%H:%M:%S')} ---")
    print(f"Scanning {len(tickers)} active symbols from cache with market trend {market_trend}...")
    
    if market_trend == "CHOPPY":
        print("[INFO] Market is currently CHOPPY. Running swing scan anyway, but setups may be volatile.")
    
    ticker_ns_list = [t + ".NS" for t in tickers]
    try:
        df_all = yf.download(ticker_ns_list, period="5d", interval="15m", group_by="ticker", progress=False, auto_adjust=True)
    except Exception as e:
        print(f"Error downloading batch data for swing scan: {e}")
        return
        
    candidates = []
    # If only 1 ticker is in list, yfinance returns flat columns instead of MultiIndex level
    if len(tickers) == 1:
        ticker = tickers[0]
        res = check_confluence_for_dataframe(ticker, df_all)
        if res:
            # Filter by trend
            if market_trend == "BULLISH" and res["direction"] != "BUY":
                pass
            elif market_trend == "BEARISH" and res["direction"] != "SELL":
                pass
            else:
                candidates.append(res)
    else:
        for ticker in tickers:
            ticker_ns = ticker + ".NS"
            try:
                if ticker_ns in df_all.columns.levels[0]:
                    df_ticker = df_all[ticker_ns].dropna()
                    if len(df_ticker) >= 50:
                        res = check_confluence_for_dataframe(ticker, df_ticker)
                        if res:
                            # Filter by trend
                            if market_trend == "BEARISH" and res["direction"] != "SELL":
                                pass
                            elif market_trend == "BULLISH" and res["direction"] != "BUY":
                                pass
                            else:
                                latest = df_ticker.iloc[-1]
                                vol_mean = df_ticker['Volume'].rolling(20).mean().iloc[-1]
                                vol_ratio = latest['Volume'] / vol_mean if vol_mean > 0 else 0
                                atr_pct = ((latest['High'] - latest['Low']) / latest['Close']) * 100
                                if 'ATR' in df_ticker.columns: atr_pct = (latest['ATR'] / latest['Close']) * 100
                                res["trade_type"] = "INTRADAY" if (atr_pct > 3.5 or vol_ratio > 2.0) else "POSITIONAL"
                                res["msg"] = res["msg"].replace("INTRADAY", res["trade_type"])
                                candidates.append(res)
                                
                    if len(df_ticker) >= 20:
                        ut_res = core.check_utbot_for_dataframe(ticker, df_ticker)
                        if ut_res:
                            direction = ut_res[2]
                            c_close = ut_res[4]
                            c_stop = ut_res[5]
                            risk = abs(c_close - c_stop)
                            if risk == 0:
                                risk = c_close * 0.01
                            
                            is_bullish = direction == "Long"
                            if market_trend == "BEARISH" and is_bullish:
                                pass
                            elif market_trend == "BULLISH" and not is_bullish:
                                pass
                            else:
                                target1 = c_close + (2 * risk) if is_bullish else c_close - (2 * risk)
                                target2 = c_close + (3 * risk) if is_bullish else c_close - (3 * risk)
                                
                                latest = df_ticker.iloc[-1]
                                vol_mean = df_ticker['Volume'].rolling(20).mean().iloc[-1]
                                vol_ratio = latest['Volume'] / vol_mean if vol_mean > 0 else 0
                                atr_pct = ((latest['High'] - latest['Low']) / latest['Close']) * 100
                                if 'ATR' in df_ticker.columns: atr_pct = (latest['ATR'] / latest['Close']) * 100
                                trade_type = "INTRADAY" if (atr_pct > 3.5 or vol_ratio > 2.0) else "POSITIONAL"
                                
                                candidates.append({
                                    "ticker": ticker,
                                    "direction": "BUY" if is_bullish else "SELL",
                                    "entry": c_close,
                                    "sl": c_stop,
                                    "target1": target1,
                                    "target2": target2,
                                    "status": "UT_BOT_BREAKOUT" if is_bullish else "UT_BOT_BREAKDOWN",
                                    "setup_date": datetime.now().strftime("%Y-%m-%d"),
                                    "pct_change": ut_res[3],
                                    "trade_type": trade_type,
                                    "msg": (f"[INFO] {trade_type} UT BOT (1, 10) {'LONG' if is_bullish else 'SHORT'} SIGNAL\n"
                                            f"Stock: #{ticker}\n"
                                            f"Live Entry Price: `{c_close:.2f}`\n"
                                            f"Stop Loss: `{c_stop:.2f}`\n"
                                            f"Target 1: `{target1:.2f}`\n"
                                            f"Target 2: `{target2:.2f}`")
                                })
            except Exception:
                pass
                
    # Sort candidates by absolute price change percentage and take the top 5
    candidates = sorted(candidates, key=lambda x: x.get("pct_change", 0.0), reverse=True)[:5]
    print(f"Swing Scan complete. Filtered to top {len(candidates)} breakout/breakdown signals (Trend matched).")
    
    pt = trades.load_paper_trades()
    active_tickers = set([t["ticker"] for t in pt if t["status"] in ["ACTIVE", "PARTIAL_EXIT", "PENDING"]])
    
    for c in candidates:
        ticker = c["ticker"]
        if ticker in active_tickers:
            continue
            
        alert_key = f"{ticker}_{c['status']}_{c['setup_date']}"
        if alert_key not in alerted_today:
            send_telegram_fn(c["msg"])
            print(c["msg"])
            alerted_today.add(alert_key)
            save_alerts_state()
            
            trades.initiate_paper_trade(ticker, {
                "entry": c["entry"],
                "sl": c["sl"],
                "target1": c["target1"],
                "target2": c["target2"],
                "direction": c["direction"],
                "setup_date": c["setup_date"],
                "trade_type": c.get("trade_type", "INTRADAY")
            })
            
            dir_label = "B/O Call" if c["direction"] in ["BUY", "LONG", "Long"] else "Short"
            new_line = f"{ticker} | Swing | {dir_label} | {c.get('trade_type', 'INTRADAY')} | {c['entry']:.2f}\n"
            
            lines = []
            updated = False
            if os.path.exists(WATCHLIST_FILE):
                with open(WATCHLIST_FILE, "r") as f:
                    for line in f:
                        if line.strip():
                            if line.split('|')[0].strip() == ticker:
                                lines.append(new_line)
                                updated = True
                            else:
                                lines.append(line)
            if not updated:
                lines.append(new_line)
                
            with open(WATCHLIST_FILE, "w") as f:
                f.writelines(lines)
