import yfinance as yf
import pandas as pd
import numpy as np
import os
import glob
import json

def check_setup(df, idx, strict=True):
    """
    Checks if a setup (NR7 + Inside Bar) exists at a given row index.
    Returns: (is_bullish, is_bearish)
    """
    try:
        latest = df.iloc[idx]
        
        # We enforce NR7 alone as the volatility contraction signal
        ranges = df['Range'].iloc[idx-6:idx+1].tolist()
        if len(ranges) < 7:
            return False, False
            
        is_nr7 = ranges[-1] == min(ranges)
        
        if not is_nr7:
            return False, False
            
        cur_vol = float(latest['Volume'])
        sma_vol = float(latest['SMA_Vol_20'])
        
        if cur_vol < sma_vol:
            return False, False
            
        setup_close = float(latest['Close'])
        sma50 = float(latest['SMA_50'])
        sma10 = float(latest['SMA_10'])
        sma200 = float(latest['SMA_200'])
        rsi = float(latest['RSI_14'])
        
        is_bullish = (setup_close > sma50) and (sma10 > sma50) and (rsi > 55)
        is_bearish = (setup_close < sma50) and (sma10 < sma50) and (rsi < 45)
                      
        return is_bullish, is_bearish
    except Exception:
        return False, False

def scan_stock(ticker, market_trend, strict=True, max_days=3):
    """Scans a single ticker and returns its tracking action and Telegram message payload."""
    try:
        df = yf.download(ticker + ".NS", period="1y", progress=False, auto_adjust=True)
        if df.empty or len(df) < 200:
            return "REMOVE", {"status": "Error: Not enough data", "trigger": False}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        df['Range'] = df['High'] - df['Low']
        df['SMA_10'] = df['Close'].rolling(window=10).mean()
        df['SMA_50'] = df['Close'].rolling(window=50).mean()
        df['SMA_200'] = df['Close'].rolling(window=200).mean()
        df['SMA_Vol_20'] = df['Volume'].rolling(window=20).mean()
        
        # RSI 14
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI_14'] = 100 - (100 / (1 + rs))
        
        # ATR 14
        df['Prev_Close'] = df['Close'].shift(1)
        df['TR'] = np.maximum(
            df['High'] - df['Low'],
            np.maximum(
                abs(df['High'] - df['Prev_Close']),
                abs(df['Low'] - df['Prev_Close'])
            )
        )
        df['ATR'] = df['TR'].rolling(14).mean()
        
        latest_idx = len(df) - 1
        
        def get_val(row, col):
            val = row[col]
            return val.iloc[0] if isinstance(val, pd.Series) else float(val)

        for days_ago in range(max_days + 1):
            idx = latest_idx - days_ago
            setup_row = df.iloc[idx]
            
            is_bullish, is_bearish = check_setup(df, idx, strict)
            latest_close = get_val(df.iloc[latest_idx], 'Close')
            
            # Enforce Nifty Market Regime Filter
            if is_bullish and market_trend == "BEARISH":
                is_bullish = False
            if is_bearish and market_trend == "BULLISH":
                is_bearish = False
                
            def get_trade_params(direction, setup_row):
                atr = get_val(setup_row, 'ATR')
                rsi = get_val(setup_row, 'RSI_14')
                close_price = get_val(setup_row, 'Close')
                atr_pct = (atr / close_price) * 100
                
                is_intraday = False
                if atr_pct > 3.5:
                    is_intraday = True
                if direction == "BUY" and rsi > 70:
                    is_intraday = True
                if direction == "SELL" and rsi < 30:
                    is_intraday = True
                    
                trade_type = "INTRADAY" if is_intraday else "POSITIONAL"
                
                if direction == "BUY":
                    entry = get_val(setup_row, 'High') + 0.10
                    sl = entry - (1.5 * atr)
                else:
                    entry = get_val(setup_row, 'Low') - 0.10
                    sl = entry + (1.5 * atr)
                    
                risk = abs(entry - sl)
                
                if is_intraday:
                    t1_mult, t2_mult = 1.0, 2.0
                else:
                    t1_mult, t2_mult = 2.0, 4.0
                    
                target1 = entry + (t1_mult * atr) if direction == "BUY" else entry - (t1_mult * atr)
                target2 = entry + (t2_mult * atr) if direction == "BUY" else entry - (t2_mult * atr)
                
                return entry, sl, target1, target2, trade_type
                
            if is_bullish:
                if days_ago == 0:
                    entry, sl, target1, target2, trade_type = get_trade_params("BUY", setup_row)
                    msg = (f"🟩 *NEW BULLISH SETUP (NR7/RSI)*\n"
                           f"Stock: #{ticker} ({trade_type})\n"
                           f"Trigger Entry (Buy Stop): `{entry:.2f}`\n"
                           f"Stop Loss: `{sl:.2f}`\n"
                           f"Target 1: `{target1:.2f}`\n"
                           f"Target 2: `{target2:.2f}`\n"
                           f"Nifty Trend: {market_trend}")
                    return "KEEP", {
                        "status": "SETUP_BULLISH", "msg": msg, "trigger": True,
                        "entry": entry, "sl": sl, "target1": target1, "target2": target2,
                        "direction": "BUY", "setup_date": df.index[idx].strftime("%Y-%m-%d"),
                        "trade_type": trade_type
                    }
                else:
                    # Pending setup
                    if latest_close < get_val(setup_row, 'High'):
                        entry, sl, target1, target2, trade_type = get_trade_params("BUY", setup_row)
                        msg = (f"💥 *ACTIVE BULLISH BREAKOUT (Buy CALL)*\n"
                               f"Stock: #{ticker} ({trade_type})\n"
                               f"Live Entry: `{entry:.2f}`\n"
                               f"Stop Loss: `{sl:.2f}`\n"
                               f"Target 1: `{target1:.2f}`\n"
                               f"Target 2: `{target2:.2f}`\n"
                               f"Nifty Trend: {market_trend}")
                        return "KEEP", {
                            "status": "ACTIVE_BULLISH", "msg": msg, "trigger": True,
                            "entry": entry, "sl": sl, "target1": target1, "target2": target2,
                            "direction": "BUY", "setup_date": df.index[idx].strftime("%Y-%m-%d"),
                            "trade_type": trade_type
                        }
                    else:
                        return "REMOVE", {"status": "Already broke out earlier", "trigger": False}
                        
                if latest_close < get_val(setup_row, 'Low'):
                    return "REMOVE", {"status": "Setup Invalidated (SL Hit)", "trigger": False}
                    
                return "KEEP", {"status": "PENDING", "trigger": False}
                
            elif is_bearish:
                if days_ago == 0:
                    entry, sl, target1, target2, trade_type = get_trade_params("SELL", setup_row)
                    msg = (f"🟥 *NEW BEARISH SETUP (NR7/RSI)*\n"
                           f"Stock: #{ticker} ({trade_type})\n"
                           f"Trigger Entry (Sell Stop): `{entry:.2f}`\n"
                           f"Stop Loss: `{sl:.2f}`\n"
                           f"Target 1: `{target1:.2f}`\n"
                           f"Target 2: `{target2:.2f}`\n"
                           f"Nifty Trend: {market_trend}")
                    return "KEEP", {
                        "status": "SETUP_BEARISH", "msg": msg, "trigger": True,
                        "entry": entry, "sl": sl, "target1": target1, "target2": target2,
                        "direction": "SELL", "setup_date": df.index[idx].strftime("%Y-%m-%d"),
                        "trade_type": trade_type
                    }
                else:
                    # Pending setup
                    if latest_close > get_val(setup_row, 'Low'):
                        entry, sl, target1, target2, trade_type = get_trade_params("SELL", setup_row)
                        msg = (f"💥 *ACTIVE BEARISH BREAKDOWN (Buy PUT)*\n"
                               f"Stock: #{ticker} ({trade_type})\n"
                               f"Live Entry: `{entry:.2f}`\n"
                               f"Stop Loss: `{sl:.2f}`\n"
                               f"Target 1: `{target1:.2f}`\n"
                               f"Target 2: `{target2:.2f}`\n"
                               f"Nifty Trend: {market_trend}")
                        return "KEEP", {
                            "status": "ACTIVE_BEARISH", "msg": msg, "trigger": True,
                            "entry": entry, "sl": sl, "target1": target1, "target2": target2,
                            "direction": "SELL", "setup_date": df.index[idx].strftime("%Y-%m-%d"),
                            "trade_type": trade_type
                        }
                    else:
                        return "REMOVE", {"status": "Already broke down earlier", "trigger": False}
                        
                if latest_close > get_val(setup_row, 'High'):
                    return "REMOVE", {"status": "Setup Invalidated (SL Hit)", "trigger": False}
                    
                return "KEEP", {"status": "PENDING", "trigger": False}
                
        return "REMOVE", {"status": f"No active setup in last {max_days} days.", "trigger": False}
        
    except Exception as e:
        return "KEEP", {"status": f"Error: {e}", "trigger": False}

def get_dynamic_tickers():
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    CONFIG_DIR = os.path.join(BASE_DIR, "config")
    NSEDATA_DIR = os.path.join(BASE_DIR, "NSEDATA")
    FO_TICKERS_FILE = os.path.join(CONFIG_DIR, "fo_tickers.json")

    tickers = set()
    if os.path.exists(FO_TICKERS_FILE):
        try:
            with open(FO_TICKERS_FILE, "r") as f:
                base_tickers = json.load(f)
                for t in base_tickers:
                    tickers.add(t)
        except Exception:
            pass

    if os.path.exists(NSEDATA_DIR):
        for csv_file in glob.glob(os.path.join(NSEDATA_DIR, "*.csv")):
            try:
                df = pd.read_csv(csv_file)
                if '% CHANGE' in df.columns and 'SYMBOL' in df.columns:
                    df['% CHANGE'] = pd.to_numeric(df['% CHANGE'], errors='coerce')
                    df = df.dropna(subset=['% CHANGE'])
                    
                    for sym in df['SYMBOL']:
                        sym = str(sym).strip()
                        if sym and sym != "NIFTY 50" and sym != "nan":
                            tickers.add(sym)
            except Exception as e:
                print(f"Error parsing {csv_file}: {e}")

    return list(tickers)

def check_utbot_for_dataframe(ticker, df, key_value=1.5, atr_period=10):
    """
    Calculates UT Bot Alerts (ATR trailing stop) and returns Buy/Sell signal.
    """
    try:
        df = df.copy()
        
        # Calculate True Range (TR)
        df['Prev_Close'] = df['Close'].shift(1)
        df['TR'] = np.maximum(
            df['High'] - df['Low'],
            np.maximum(
                abs(df['High'] - df['Prev_Close']),
                abs(df['Low'] - df['Prev_Close'])
            )
        )
        
        df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
        
        # TradingView uses RMA (smoothed moving average) for ATR
        df['ATR'] = df['TR'].ewm(alpha=1/atr_period, adjust=False).mean()
        df['nLoss'] = key_value * df['ATR']
        
        trailing_stop = np.zeros(len(df))
        position = np.zeros(len(df))
        
        closes = df['Close'].values
        nLosses = df['nLoss'].values
        
        for i in range(1, len(df)):
            prev_ts = trailing_stop[i-1]
            c = closes[i]
            prev_c = closes[i-1]
            loss = nLosses[i]
            
            if c > prev_ts and prev_c > prev_ts:
                trailing_stop[i] = max(prev_ts, c - loss)
            elif c < prev_ts and prev_c < prev_ts:
                trailing_stop[i] = min(prev_ts, c + loss)
            elif c > prev_ts:
                trailing_stop[i] = c - loss
            else:
                trailing_stop[i] = c + loss
                
            if c > trailing_stop[i] and prev_c <= trailing_stop[i-1]:
                position[i] = 1
            elif c < trailing_stop[i] and prev_c >= trailing_stop[i-1]:
                position[i] = -1
            else:
                position[i] = position[i-1]
                
        df['UTBot_Stop'] = trailing_stop
        df['Position'] = position
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        is_bullish_cross = latest['Position'] == 1 and prev['Position'] != 1
        is_bearish_cross = latest['Position'] == -1 and prev['Position'] != -1
        
        c_close = float(latest['Close'])
        c_stop = float(latest['UTBot_Stop'])
        c_ema50 = float(latest['EMA_50'])
        
        pct_change = abs((c_close - float(prev['Close'])) / float(prev['Close'])) * 100.0 if float(prev['Close']) > 0 else 0.0
        
        risk = abs(c_close - c_stop)
        if risk == 0: risk = c_close * 0.01
        
        if is_bullish_cross and c_close > c_ema50:
            t1 = c_close + (2 * risk)
            t2 = c_close + (3 * risk)
            return ticker, "UT_Bot", "Long", pct_change, c_close, c_stop, t1, t2
        elif is_bearish_cross and c_close < c_ema50:
            t1 = c_close - (2 * risk)
            t2 = c_close - (3 * risk)
            return ticker, "UT_Bot", "Short", pct_change, c_close, c_stop, t1, t2
            
    except Exception as e:
        print(f"Error calculating UT Bot for {ticker}: {e}")
    return None

def get_market_regime():
    """
    Determines the broader market regime by evaluating ADX for trend strength
    and Nifty 50 SMA alignments.
    Returns: 'BULLISH', 'BEARISH', or 'CHOPPY' (Sideways).
    """
    try:
        df = yf.download("^NSEI", period="6mo", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 50:
            return "NEUTRAL"
        
        if isinstance(df.columns, pd.MultiIndex):
            # Target the specific ticker symbol's cross section directly to avoid duplicate column ambiguities
            df = df.xs("^NSEI", level=1, axis=1)
            
        # Moving Averages
        df['SMA_20'] = df['Close'].rolling(20).mean()
        df['SMA_50'] = df['Close'].rolling(50).mean()
        
        # ADX Calculation (14-period)
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
        
        df['+DM'] = np.where((df['High'] - df['High'].shift(1)) > (df['Low'].shift(1) - df['Low']), 
                             np.maximum(df['High'] - df['High'].shift(1), 0), 0)
        df['-DM'] = np.where((df['Low'].shift(1) - df['Low']) > (df['High'] - df['High'].shift(1)), 
                             np.maximum(df['Low'].shift(1) - df['Low'], 0), 0)
                             
        # Smoothed True Range and Directional Movement
        df['TR_14'] = df['TR'].rolling(14).sum()
        df['+DM_14'] = df['+DM'].rolling(14).sum()
        df['-DM_14'] = df['-DM'].rolling(14).sum()
        
        df['+DI'] = 100 * (df['+DM_14'] / df['TR_14'])
        df['-DI'] = 100 * (df['-DM_14'] / df['TR_14'])
        
        df['DX'] = 100 * abs((df['+DI'] - df['-DI']) / (df['+DI'] + df['-DI']))
        df['ADX'] = df['DX'].rolling(14).mean()
        
        latest_close = float(df['Close'].iloc[-1])
        latest_sma20 = float(df['SMA_20'].iloc[-1])
        latest_sma50 = float(df['SMA_50'].iloc[-1])
        latest_adx = float(df['ADX'].iloc[-1])
        
        # Explicit choppy filter based on ADX
        if latest_adx < 20.0:
            return "CHOPPY"
        
        # Strong Trend Validation
        if latest_close > latest_sma50 and latest_sma20 > latest_sma50:
            return "BULLISH"
        elif latest_close < latest_sma50 and latest_sma20 < latest_sma50:
            return "BEARISH"
            
        return "CHOPPY"
        
    except Exception as e:
        print(f"Error determining market regime: {e}")
        
    return "CHOPPY"

def predict_future_trend(df, forward_days=5):
    """
    Calculates a linear regression over the last 20 closing prices
    to project the expected percentage change over the next `forward_days`.
    Returns a string like 'PROJ_UP_2.4' or 'PROJ_DOWN_1.2'
    """
    try:
        if len(df) < 20:
            return "PROJ_NONE_0.0"
        
        recent_closes = df['Close'].tail(20).values
        x = np.arange(20)
        
        slope, intercept = np.polyfit(x, recent_closes, 1)
        
        future_x = 19 + forward_days
        projected_price = intercept + (slope * future_x)
        current_price = recent_closes[-1]
        
        pct_change = ((projected_price - current_price) / current_price) * 100.0
        
        try:
            pct_val = float(np.squeeze(pct_change))
        except:
            pct_val = float(pct_change[0]) if hasattr(pct_change, '__getitem__') else 0.0
            
        if pct_val >= 0:
            return f"PROJ_UP_{pct_val:.1f}"
        else:
            return f"PROJ_DOWN_{abs(pct_val):.1f}"
            
    except Exception as e:
        print(f"Error predicting future trend: {e}")
        return "PROJ_NONE_0.0"
