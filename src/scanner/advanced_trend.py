import os
import json
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from scanner import trades, core

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")

FO_TICKERS_FILE = os.path.join(CONFIG_DIR, "fo_tickers.json")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.txt")
ALERTS_STATE_FILE = os.path.join(DATA_DIR, "advanced_trend_alerts.json")

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

def calculate_adx(df, period=14):
    df = df.copy()
    df['H-L'] = df['High'] - df['Low']
    df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
    df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
    df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
    
    df['+DM'] = np.where((df['High'] - df['High'].shift(1)) > (df['Low'].shift(1) - df['Low']), 
                         np.maximum(df['High'] - df['High'].shift(1), 0), 0)
    df['-DM'] = np.where((df['Low'].shift(1) - df['Low']) > (df['High'] - df['High'].shift(1)), 
                         np.maximum(df['Low'].shift(1) - df['Low'], 0), 0)
                         
    df['TR_14'] = df['TR'].rolling(period).sum()
    df['+DM_14'] = df['+DM'].rolling(period).sum()
    df['-DM_14'] = df['-DM'].rolling(period).sum()
    
    df['+DI'] = 100 * (df['+DM_14'] / df['TR_14'])
    df['-DI'] = 100 * (df['-DM_14'] / df['TR_14'])
    
    df['DX'] = 100 * abs((df['+DI'] - df['-DI']) / (df['+DI'] + df['-DI']))
    df['ADX'] = df['DX'].rolling(period).mean()
    return df['ADX']

def calculate_rsi(df, period=14):
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))
    
def check_advanced_trend_for_dataframe(ticker, df):
    """
    Evaluates UT Bot (EMA cross), WaveTrend (RSI), Squeeze (BB width), ADX, and Chop Zone (ATR).
    """
    try:
        df = df.copy()
        
        # Calculate EMA 10 and 20
        df['EMA_10'] = df['Close'].ewm(span=10, adjust=False).mean()
        df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
        
        # Calculate RSI 14
        df['RSI_14'] = calculate_rsi(df, 14)
        
        # Calculate Bollinger Bands (20, 2)
        df['SMA_20'] = df['Close'].rolling(window=20).mean()
        df['STD_20'] = df['Close'].rolling(window=20).std()
        df['Upper_BB'] = df['SMA_20'] + (2 * df['STD_20'])
        df['Lower_BB'] = df['SMA_20'] - (2 * df['STD_20'])
        df['BB_Width'] = (df['Upper_BB'] - df['Lower_BB']) / df['Close']
        
        # Calculate ADX 14
        df['ADX_14'] = calculate_adx(df, 14)
        
        # Calculate ATR 14 for Chop zone
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
        df['ATR_14'] = df['TR'].rolling(14).mean()
        df['ATR_Pct'] = df['ATR_14'] / df['Close']
        
        if len(df) < 2:
            return None
            
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        c_close = float(latest['Close'])
        
        if c_close < 200:
            return None
            
        # Core checks based on Chartink INDMONEY logic
        bb_width_ok = float(latest['BB_Width']) <= 0.10
        adx_ok = float(latest['ADX_14']) > 20
        atr_ok = float(latest['ATR_Pct']) > 0.002
        
        c_ema10 = float(latest['EMA_10'])
        c_ema20 = float(latest['EMA_20'])
        c_ema50 = float(latest['EMA_50'])
        p_ema10 = float(prev['EMA_10'])
        p_ema20 = float(prev['EMA_20'])
        
        c_rsi = float(latest['RSI_14'])
        p_rsi = float(prev['RSI_14'])
        
        is_bullish_cross = (c_ema10 > c_ema20) and (p_ema10 <= p_ema20)
        is_bearish_cross = (c_ema10 < c_ema20) and (p_ema10 >= p_ema20)
        
        is_bullish = (
            is_bullish_cross and
            c_close > c_ema50 and
            c_rsi > p_rsi and
            bb_width_ok and
            adx_ok and
            atr_ok
        )
        
        is_bearish = (
            is_bearish_cross and
            c_close < c_ema50 and
            c_rsi < p_rsi and
            bb_width_ok and
            adx_ok and
            atr_ok
        )
        
        # Calculate Daily Pivot Points for Targets and SL
        unique_dates = sorted(list(set(df.index.date)))
        atr_val = float(latest['ATR_14'])
        buffer_pts = atr_val * 1.5 
        has_pivots = False
        if len(unique_dates) >= 2:
            yesterday_date = unique_dates[-2]
            df_y = df[df.index.date == yesterday_date]
            if not df_y.empty:
                y_high = float(df_y['High'].max())
                y_low = float(df_y['Low'].min())
                y_close = float(df_y['Close'].iloc[-1])
                
                P = (y_high + y_low + y_close) / 3.0
                R1 = (2 * P) - y_low
                S1 = (2 * P) - y_high
                R2 = P + (y_high - y_low)
                S2 = P - (y_high - y_low)
                R3 = R2 + (y_high - y_low)
                S3 = S2 - (y_high - y_low)
                
                pivot_levels = sorted([S3, S2, S1, P, R1, R2, R3])
                has_pivots = True

        atr_val = float(latest['ATR_14'])
        buffer_pts = atr_val * 0.5 # Dynamic volatility buffer for SL
        
        if is_bullish:
            entry = c_close
            sl = entry - (1.5 * atr_val) # Fallback
            target1 = entry + (2 * atr_val)
            target2 = entry + (3 * atr_val)
            
            if has_pivots:
                below_levels = [lvl for lvl in pivot_levels if lvl < entry]
                if below_levels:
                    proposed_sl = below_levels[-1] - buffer_pts
                    sl = proposed_sl if (entry - proposed_sl) >= (1.5 * atr_val) else entry - (1.5 * atr_val)
                
                above_levels = [lvl for lvl in pivot_levels if lvl > entry]
                if above_levels:
                    target1 = above_levels[0]
                    target2 = above_levels[1] if len(above_levels) > 1 else above_levels[0] + (above_levels[0] - entry)
            
            risk = entry - sl
            if risk <= 0: risk = entry * 0.01
            
            return {
                "ticker": ticker,
                "direction": "BUY",
                "entry": entry,
                "sl": sl,
                "target1": target1,
                "target2": target2,
                "status": "ADVANCED_TREND_LONG",
                "setup_date": datetime.now().strftime("%Y-%m-%d"),
                "msg": (f"[INFO] ⚡ INTRADAY ADVANCED TREND (BULLISH)\n"
                        f"Stock: #{ticker}\n"
                        f"Live Entry: `{entry:.2f}`\n"
                        f"Pivot SL: `{sl:.2f}`\n"
                        f"Pivot T1: `{target1:.2f}`\n"
                        f"Pivot T2: `{target2:.2f}`")
            }
            
        elif is_bearish:
            entry = c_close
            sl = entry + (1.5 * atr_val) # Fallback
            target1 = entry - (2 * atr_val)
            target2 = entry - (3 * atr_val)
            
            if has_pivots:
                above_levels = [lvl for lvl in pivot_levels if lvl > entry]
                if above_levels:
                    proposed_sl = above_levels[0] + buffer_pts
                    sl = proposed_sl if (proposed_sl - entry) >= (1.5 * atr_val) else entry + (1.5 * atr_val)
                    
                below_levels = sorted([lvl for lvl in pivot_levels if lvl < entry], reverse=True)
                if below_levels:
                    target1 = below_levels[0]
                    target2 = below_levels[1] if len(below_levels) > 1 else below_levels[0] - (entry - below_levels[0])
            
            risk = sl - entry
            if risk <= 0: risk = entry * 0.01
            
            return {
                "ticker": ticker,
                "direction": "SELL",
                "entry": entry,
                "sl": sl,
                "target1": target1,
                "target2": target2,
                "status": "ADVANCED_TREND_SHORT",
                "setup_date": datetime.now().strftime("%Y-%m-%d"),
                "msg": (f"[INFO] 🩸 INTRADAY ADVANCED TREND (BEARISH)\n"
                        f"Stock: #{ticker}\n"
                        f"Live Entry: `{entry:.2f}`\n"
                        f"Pivot SL: `{sl:.2f}`\n"
                        f"Pivot T1: `{target1:.2f}`\n"
                        f"Pivot T2: `{target2:.2f}`")
            }
            
    except Exception as e:
        print(f"Error checking advanced trend for {ticker}: {e}")
        
    return None

def execute_advanced_trend_scan(send_telegram_fn, market_trend="NEUTRAL"):
    """Executes the Advanced Trend scan on a 15-minute timeframe."""
    load_alerts_state()
    
    tickers = core.get_dynamic_tickers()
    if not tickers:
        print("[INFO] F&O tickers list is empty for Advanced Trend Scan.")
        return
        
    print(f"\n--- [INFO] Running Scheduled Advanced Trend Scan @ {datetime.now().strftime('%H:%M:%S')} ---")
    
    if market_trend == "CHOPPY":
        print("[INFO] Market is currently CHOPPY. Running advanced trend scan anyway, but setups may be volatile.")
        
    ticker_ns_list = [t + ".NS" for t in tickers]
    try:
        # Need at least 50 bars to correctly calculate ADX 14 and EMA 20 over a 15m period
        df_all = yf.download(ticker_ns_list, period="5d", interval="15m", group_by="ticker", progress=False, auto_adjust=True)
    except Exception as e:
        print(f"Error downloading batch data for advanced trend scan: {e}")
        return
        
    candidates = []
    if len(tickers) == 1:
        ticker = tickers[0]
        res = check_advanced_trend_for_dataframe(ticker, df_all)
        if res:
            if market_trend == "BEARISH" and res["direction"] != "SELL":
                pass
            elif market_trend == "BULLISH" and res["direction"] != "BUY":
                pass
            else:
                candidates.append(res)
    else:
        for ticker in tickers:
            ticker_ns = ticker + ".NS"
            try:
                if ticker_ns in df_all.columns.levels[0]:
                    df_ticker = df_all[ticker_ns].dropna()
                    if len(df_ticker) >= 30:
                        res = check_advanced_trend_for_dataframe(ticker, df_ticker)
                        if res:
                            # Filter by overall trend
                            if market_trend == "BEARISH" and res["direction"] != "SELL":
                                pass
                            elif market_trend == "BULLISH" and res["direction"] != "BUY":
                                pass
                            else:
                                candidates.append(res)
            except Exception:
                pass
                
    pt = trades.load_paper_trades()
    active_tickers = set([t["ticker"] for t in pt if t["status"] in ["ACTIVE", "PARTIAL_EXIT", "PENDING"]])
    
    new_alerts = []
    for c in candidates:
        ticker = c["ticker"]
        if ticker in active_tickers:
            continue
            
        alert_key = f"{ticker}_{c['status']}_{c['setup_date']}"
        if alert_key not in alerted_today:
            new_alerts.append(c)
            alerted_today.add(alert_key)
            print(c["msg"])
            
            trades.initiate_paper_trade(ticker, {
                "entry": c["entry"],
                "sl": c["sl"],
                "target1": c["target1"],
                "target2": c["target2"],
                "direction": c["direction"],
                "setup_date": c["setup_date"],
                "trade_type": "INTRADAY"
            })
            
            dir_label = "B/O Call" if c["direction"] in ["BUY", "LONG", "Long"] else "Short"
            new_line = f"{ticker} | AdvancedTrend | {dir_label} | INTRADAY | {c['entry']:.2f}\n"
            
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

    if new_alerts:
        save_alerts_state()
        lines_out = [f"⚡ *VYUHA INTRADAY TREND SIGNALS* ({len(new_alerts)} Setups)\n"]
        for c in new_alerts:
            action = "🟢 BUY" if c["direction"] == "BUY" else "🔴 SELL"
            lines_out.append(
                f"• *{action}: #{c['ticker']}*\n"
                f"  Entry: `{c['entry']:.2f}` | SL: `{c['sl']:.2f}` | T1: `{c['target1']:.2f}` | T2: `{c['target2']:.2f}`\n"
                f"  Reason: 15m Cloud & Momentum | Hold: 1–4 Hours"
            )
        combined_msg = "\n\n".join(lines_out)
        send_telegram_fn(combined_msg)

    print(f"Advanced Trend Scan complete. Generated {len(candidates)} valid signals.")
