import os
import json
import csv
import pandas as pd
from datetime import datetime
from db_cache import get_cached_yfinance
import db_storage

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

def remove_from_watchlist(ticker_to_remove):
    db_storage.remove_from_watchlist(ticker_to_remove)

def load_paper_trades():
    return db_storage.get_paper_trades()

def save_paper_trades(trades):
    db_storage.save_paper_trades(trades)

def evaluate_daily_trend(ticker, direction):
    """
    Downloads the daily chart for the ticker. 
    Returns True if the Daily EMA10 > EMA20 and RSI > 50 for Longs (or < for Shorts).
    """
    try:
        ticker_ns = ticker + ".NS"
        df = get_cached_yfinance(ticker_ns, period="3mo", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 20:
            return False
            
        df = df.copy()
        df['EMA_10'] = df['Close'].ewm(span=10, adjust=False).mean()
        df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
        
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI_14'] = 100 - (100 / (1 + rs))
        
        latest = df.iloc[-1]
        c_ema10 = float(latest['EMA_10'])
        c_ema20 = float(latest['EMA_20'])
        c_rsi = float(latest['RSI_14'])
        
        if direction == "BUY":
            return (c_ema10 > c_ema20) and (c_rsi > 50)
        else:
            return (c_ema10 < c_ema20) and (c_rsi < 50)
    except Exception as e:
        print(f"Error evaluating daily trend for {ticker}: {e}")
        return False

def log_closed_trade(ticker, direction, entry, sl, target1, target2, exit_price, pnl, result, reason, duration_days, trade_type="POSITIONAL", strategy="NR7"):
    db_storage.log_closed_trade(ticker, direction, entry, sl, target1, target2, exit_price, pnl, result, reason, duration_days, trade_type, strategy)

def initiate_paper_trade(ticker, result):
    """Initiates a new paper trade tracking entry in paper_trades.json."""
    trades = load_paper_trades()
    
    # Check if we already have an active/pending trade for this symbol to avoid duplicates
    active_count = 0
    pending_count = 0
    
    for t in trades:
        if t["status"] in ["ACTIVE", "PARTIAL_EXIT"]:
            active_count += 1
        elif t["status"] == "PENDING":
            pending_count += 1
            
        if t["ticker"] == ticker and t["status"] in ["PENDING", "ACTIVE", "PARTIAL_EXIT"]:
            print(f"Trade already exists for {ticker} (Status: {t['status']}). Skipping initiation.")
            return
            
    if active_count >= 2:
        print(f"Global limit reached: {active_count} ACTIVE positions. Skipping initiation for {ticker}.")
        return
        
    # Removed global pending limit to allow EOD scanner to populate pending setups
            
    # Calculate quantity based on risk: Risk ₹1,000 per trade
    risk_per_share = abs(result['entry'] - result['sl'])
    qty = 100
    if risk_per_share > 0:
        qty = int(1000 / risk_per_share)
        
    new_trade = {
        "ticker": ticker,
        "direction": result['direction'],
        "status": "PENDING",
        "entry_price": result['entry'],
        "initial_sl": result['sl'],
        "current_sl": result['sl'],
        "target1": result['target1'],
        "target2": result['target2'],
        "quantity": qty,
        "setup_date": result['setup_date'],
        "entry_date": None,
        "exit_date": None,
        "pnl": 0.0,
        "trade_type": result.get('trade_type', 'POSITIONAL'),
        "strategy": result.get('strategy', 'NR7'),
        "is_real_trade": False
    }
    
    trades.append(new_trade)
    save_paper_trades(trades)
    print(f"Paper trade initiated for {ticker} (Quantity: {qty})")

def update_and_manage_trades(send_telegram_fn):
    """Loops through all open paper trades and updates their status based on current market data."""
    trades = load_paper_trades()
    if not trades:
        return
        
    from connectors import angelone
    
    updated_trades = []
    has_changes = False
    
    active_trades = [t for t in trades if t["status"] in ["PENDING", "ACTIVE", "PARTIAL_EXIT"]]
    active_tickers = list(set([t["ticker"] for t in active_trades]))
    
    live_quotes = {}
    if angelone.is_authenticated and active_tickers:
        # Silently fetch live feed in the background
        live_quotes = angelone.get_live_ohlc(active_tickers)
    
    for trade in trades:
        if trade["status"] == "CLOSED":
            updated_trades.append(trade)
            continue
            
        ticker = trade["ticker"]
        direction = trade["direction"]
        
        try:
            high, low, close, date_str = None, None, None, None
            trading_days_elapsed = 0
            
            if ticker in live_quotes:
                quote = live_quotes[ticker]
                high = quote["high"]
                low = quote["low"]
                close = quote["last_price"]
                date_str = datetime.now().strftime("%Y-%m-%d")
                
                if trade.get("setup_date"):
                    try:
                        setup_dt = datetime.strptime(trade["setup_date"], "%Y-%m-%d")
                        calendar_days = (datetime.now() - setup_dt).days
                        trading_days_elapsed = int(calendar_days * 5 / 7)
                    except Exception:
                        pass
            else:
                df = get_cached_yfinance(ticker + ".NS", period="1mo", progress=False, auto_adjust=True)
                if df.empty:
                    updated_trades.append(trade)
                    continue
                    
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                    
                latest = df.iloc[-1]
                high = float(latest["High"])
                low = float(latest["Low"])
                close = float(latest["Close"])
                date_str = df.index[-1].strftime("%Y-%m-%d")
                
                setup_date = trade.get("setup_date")
                if setup_date in df.index.strftime("%Y-%m-%d"):
                    setup_idx = df.index.strftime("%Y-%m-%d").get_loc(setup_date)
                    trading_days_elapsed = len(df) - 1 - setup_idx
            
            if trade["status"] == "PENDING":
                trade_type = trade.get("trade_type", "POSITIONAL")
                max_wait = 0 if trade_type == "INTRADAY" else 5
                
                if trading_days_elapsed > max_wait:
                    trade["status"] = "CLOSED"
                    trade["exit_date"] = date_str
                    reason_msg = "Expired at End of Day." if max_wait == 0 else f"Not triggered within {max_wait} trading days."
                    msg = f"[INFO] SETUP EXPIRED (CANCELLED)\nStock: #{ticker}\nReason: {reason_msg}"
                    print(msg)
                    send_telegram_fn(msg)
                    remove_from_watchlist(ticker)
                    has_changes = True
                else:
                    triggered = False
                    breached = False
                    entry_p = trade["entry_price"]
                    sl = trade["initial_sl"]
                    
                    if direction == "BUY":
                        if low <= sl:
                            breached = True
                        elif high >= entry_p:
                            triggered = True
                    elif direction == "SELL":
                        if high >= sl:
                            breached = True
                        elif low <= entry_p:
                            triggered = True
                            
                    if breached and not triggered:
                        trade["status"] = "CLOSED"
                        trade["exit_date"] = date_str
                        msg = f"[INFO] SETUP BREACHED (CANCELLED)\nStock: #{ticker}\nReason: Price hit Stop Loss ({sl:.2f}) before Entry."
                        print(msg)
                        send_telegram_fn(msg)
                        remove_from_watchlist(ticker)
                        has_changes = True
                    elif triggered:
                        trade["status"] = "ACTIVE"
                        trade["entry_date"] = date_str
                        dir_label = "BUY / Buy CALL" if direction == "BUY" else "SELL / Buy PUT"
                        msg = (f"[INFO] PAPER TRADE TRIGGERED (REALTIME ENTRY)\n"
                               f"Stock: #{ticker} ({dir_label})\n"
                               f"Entry Price: `{entry_p:.2f}`\n"
                               f"Initial Stop Loss: `{trade['initial_sl']:.2f}`\n"
                               f"Target 1 (1:2): `{trade['target1']:.2f}`\n"
                               f"Target 2 (1:3): `{trade['target2']:.2f}`")
                        print(msg)
                        send_telegram_fn(msg)
                        has_changes = True
                        
            elif trade["status"] in ["ACTIVE", "PARTIAL_EXIT"]:
                entry_p = trade["entry_price"]
                qty = trade["quantity"]
                initial_sl = trade["initial_sl"]
                current_sl = trade["current_sl"]
                t1 = trade["target1"]
                t2 = trade["target2"]
                status = trade["status"]
                
                duration = 0
                if trade.get("entry_date"):
                    try:
                        entry_dt = datetime.strptime(trade["entry_date"], "%Y-%m-%d")
                        current_dt = datetime.strptime(date_str, "%Y-%m-%d")
                        duration = (current_dt - entry_dt).days
                    except Exception:
                        pass
                
                if direction == "BUY":
                    if status == "ACTIVE":
                        # Check Intraday Time Stop first
                        now_time = datetime.now().time()
                        is_past_cutoff = now_time >= datetime.strptime("15:15:00", "%H:%M:%S").time()
                        is_carry_over = trade.get("entry_date") and trade["entry_date"] < date_str
                        
                        if trade.get("trade_type") == "INTRADAY" and (is_past_cutoff or is_carry_over):
                            # Only attempt carry-over at the actual cut-off today, not on stale carry-overs
                            if is_past_cutoff and not is_carry_over and evaluate_daily_trend(ticker, "BUY"):
                                trade["trade_type"] = "POSITIONAL"
                                msg = (f"[INFO] HOLDING OVERNIGHT (CONVERSION)\n"
                                       f"Stock: #{ticker}\n"
                                       f"Reason: Daily trend is strong (EMA 10>20, RSI>50)\n"
                                       f"Type: INTRADAY -> POSITIONAL")
                                print(msg)
                                send_telegram_fn(msg)
                                has_changes = True
                                # Wait for targets as usual
                            else:
                                trade["status"] = "CLOSED"
                                trade["exit_date"] = date_str
                                pnl = (close - entry_p) * qty
                                result_lbl = "PROFIT" if pnl > 0 else "LOSS"
                                log_closed_trade(ticker, direction, entry_p, initial_sl, t1, t2, close, pnl, result_lbl, "INTRADAY_TIME_STOP", duration, trade.get("trade_type", "POSITIONAL"), trade.get("strategy", "NR7"))
                                msg = (f"[INFO] INTRADAY TIME STOP (EXIT)\n"
                                       f"Stock: #{ticker}\n"
                                       f"Exit Price: `{close:.2f}` (At Close)\n"
                                       f"PnL: `Rs.{pnl:.2f}`\n"
                                       f"Result: {result_lbl}")
                                print(msg)
                                send_telegram_fn(msg)
                                remove_from_watchlist(ticker)
                                has_changes = True
                                continue
                        
                        current_active_sl = trade.get("current_sl", initial_sl)
                        risk = entry_p - initial_sl
                        
                        if high >= entry_p + risk and current_active_sl < entry_p:
                            trade["current_sl"] = entry_p
                            current_active_sl = entry_p
                            msg = (f"[INFO] TRAILING SL (1R HIT)\n"
                                   f"Stock: #{ticker} (BUY)\n"
                                   f"Action: Trailed SL to Breakeven @ `{entry_p:.2f}`")
                            print(msg)
                            send_telegram_fn(msg)
                            has_changes = True

                        if low <= current_active_sl:
                            trade["status"] = "CLOSED"
                            trade["exit_date"] = date_str
                            pnl = (current_active_sl - entry_p) * qty
                            log_closed_trade(ticker, direction, entry_p, initial_sl, t1, t2, current_active_sl, pnl, "LOSS" if pnl < 0 else "BREAKEVEN", "HIT_SL", duration, trade.get("trade_type", "POSITIONAL"), trade.get("strategy", "NR7"))
                            msg = (f"[INFO] STOP LOSS HIT (EXIT)\n"
                                   f"Stock: #{ticker}\n"
                                   f"Exit Price: `{current_active_sl:.2f}`\n"
                                   f"PnL: `Rs.{pnl:.2f}`\n"
                                   f"Result: LOSS" if pnl < 0 else f"Result: BREAKEVEN")
                            print(msg)
                            send_telegram_fn(msg)
                            remove_from_watchlist(ticker)
                            has_changes = True
                        elif high >= t1:
                            trade["status"] = "PARTIAL_EXIT"
                            trade["current_sl"] = entry_p
                            pnl = (t1 - entry_p) * (qty / 2)
                            log_closed_trade(ticker, direction, entry_p, initial_sl, t1, t2, t1, pnl, "PROFIT", "HIT_TARGET_1", duration, trade.get("trade_type", "POSITIONAL"), trade.get("strategy", "NR7"))
                            msg = (f"[INFO] PARTIAL TARGET 1 HIT (50% BOOKED)\n"
                                   f"Stock: #{ticker}\n"
                                   f"Exit Price: `{t1:.2f}` (T1)\n"
                                   f"PnL Booked: `Rs.{pnl:.2f}`\n"
                                   f"Action: Trailed Stop Loss to Breakeven @ `{entry_p:.2f}`")
                            print(msg)
                            send_telegram_fn(msg)
                            has_changes = True
                            
                    elif status == "PARTIAL_EXIT":
                        if low <= current_sl:
                            trade["status"] = "CLOSED"
                            trade["exit_date"] = date_str
                            pnl = (current_sl - entry_p) * (qty / 2)
                            log_closed_trade(ticker, direction, entry_p, initial_sl, t1, t2, current_sl, pnl, "BREAKEVEN", "HIT_SL_BREAKEVEN", duration, trade.get("trade_type", "POSITIONAL"), trade.get("strategy", "NR7"))
                            msg = (f"[INFO] BREAKEVEN SL HIT (EXIT)\n"
                                   f"Stock: #{ticker}\n"
                                   f"Exit Price: `{current_sl:.2f}` (Breakeven SL)\n"
                                   f"PnL Booked: `Rs.{pnl:.2f}`\n"
                                   f"Result: BREAKEVEN")
                            print(msg)
                            send_telegram_fn(msg)
                            remove_from_watchlist(ticker)
                            has_changes = True
                        elif high >= t2:
                            trade["status"] = "CLOSED"
                            trade["exit_date"] = date_str
                            pnl = (t2 - entry_p) * (qty / 2)
                            log_closed_trade(ticker, direction, entry_p, initial_sl, t1, t2, t2, pnl, "PROFIT", "HIT_TARGET_2", duration, trade.get("trade_type", "POSITIONAL"), trade.get("strategy", "NR7"))
                            msg = (f"[INFO] TARGET 2 HIT (FULL EXIT)\n"
                                   f"Stock: #{ticker}\n"
                                   f"Exit Price: `{t2:.2f}` (T2)\n"
                                   f"PnL Booked: `Rs.{pnl:.2f}`\n"
                                   f"Result: PROFIT")
                            print(msg)
                            send_telegram_fn(msg)
                            has_changes = True
                            
                elif direction == "SELL":
                    if status == "ACTIVE":
                        # Check Intraday Time Stop first
                        now_time = datetime.now().time()
                        is_past_cutoff = now_time >= datetime.strptime("15:15:00", "%H:%M:%S").time()
                        is_carry_over = trade.get("entry_date") and trade["entry_date"] < date_str
                        
                        if trade.get("trade_type") == "INTRADAY" and (is_past_cutoff or is_carry_over):
                            if is_past_cutoff and not is_carry_over and evaluate_daily_trend(ticker, "SELL"):
                                trade["trade_type"] = "POSITIONAL"
                                msg = (f"[INFO] HOLDING OVERNIGHT (CONVERSION)\n"
                                       f"Stock: #{ticker}\n"
                                       f"Reason: Daily trend is strong (EMA 10<20, RSI<50)\n"
                                       f"Type: INTRADAY -> POSITIONAL")
                                print(msg)
                                send_telegram_fn(msg)
                                has_changes = True
                                # Wait for targets as usual
                            else:
                                trade["status"] = "CLOSED"
                                trade["exit_date"] = date_str
                                pnl = (entry_p - close) * qty
                                result_lbl = "PROFIT" if pnl > 0 else "LOSS"
                                log_closed_trade(ticker, direction, entry_p, initial_sl, t1, t2, close, pnl, result_lbl, "INTRADAY_TIME_STOP", duration, trade.get("trade_type", "POSITIONAL"), trade.get("strategy", "NR7"))
                                msg = (f"[INFO] INTRADAY TIME STOP (EXIT)\n"
                                       f"Stock: #{ticker}\n"
                                       f"Exit Price: `{close:.2f}` (At Close)\n"
                                       f"PnL: `Rs.{pnl:.2f}`\n"
                                       f"Result: {result_lbl}")
                                print(msg)
                                send_telegram_fn(msg)
                                remove_from_watchlist(ticker)
                                has_changes = True
                                continue
                            
                        current_active_sl = trade.get("current_sl", initial_sl)
                        risk = initial_sl - entry_p
                        
                        if low <= entry_p - risk and current_active_sl > entry_p:
                            trade["current_sl"] = entry_p
                            current_active_sl = entry_p
                            msg = (f"[INFO] TRAILING SL (1R HIT)\n"
                                   f"Stock: #{ticker} (SELL)\n"
                                   f"Action: Trailed SL to Breakeven @ `{entry_p:.2f}`")
                            print(msg)
                            send_telegram_fn(msg)
                            has_changes = True

                        if high >= current_active_sl:
                            trade["status"] = "CLOSED"
                            trade["exit_date"] = date_str
                            pnl = (entry_p - current_active_sl) * qty
                            log_closed_trade(ticker, direction, entry_p, initial_sl, t1, t2, current_active_sl, pnl, "LOSS" if pnl < 0 else "BREAKEVEN", "HIT_SL", duration, trade.get("trade_type", "POSITIONAL"), trade.get("strategy", "NR7"))
                            msg = (f"[INFO] STOP LOSS HIT (EXIT)\n"
                                   f"Stock: #{ticker}\n"
                                   f"Exit Price: `{current_active_sl:.2f}`\n"
                                   f"PnL: `Rs.{pnl:.2f}`\n"
                                   f"Result: LOSS" if pnl < 0 else f"Result: BREAKEVEN")
                            print(msg)
                            send_telegram_fn(msg)
                            has_changes = True
                        elif low <= t1:
                            trade["status"] = "PARTIAL_EXIT"
                            trade["current_sl"] = entry_p
                            pnl = (entry_p - t1) * (qty / 2)
                            log_closed_trade(ticker, direction, entry_p, initial_sl, t1, t2, t1, pnl, "PROFIT", "HIT_TARGET_1", duration, trade.get("trade_type", "POSITIONAL"), trade.get("strategy", "NR7"))
                            msg = (f"[INFO] PARTIAL TARGET 1 HIT (50% BOOKED)\n"
                                   f"Stock: #{ticker}\n"
                                   f"Exit Price: `{t1:.2f}` (T1)\n"
                                   f"PnL Booked: `Rs.{pnl:.2f}`\n"
                                   f"Action: Trailed Stop Loss to Breakeven @ `{entry_p:.2f}`")
                            print(msg)
                            send_telegram_fn(msg)
                            has_changes = True
                            
                    elif status == "PARTIAL_EXIT":
                        if high >= current_sl:
                            trade["status"] = "CLOSED"
                            trade["exit_date"] = date_str
                            pnl = (entry_p - current_sl) * (qty / 2)
                            log_closed_trade(ticker, direction, entry_p, initial_sl, t1, t2, current_sl, pnl, "BREAKEVEN", "HIT_SL_BREAKEVEN", duration, trade.get("trade_type", "POSITIONAL"), trade.get("strategy", "NR7"))
                            msg = (f"[INFO] BREAKEVEN SL HIT (EXIT)\n"
                                   f"Stock: #{ticker}\n"
                                   f"Exit Price: `{current_sl:.2f}` (Breakeven SL)\n"
                                   f"PnL Booked: `Rs.{pnl:.2f}`\n"
                                   f"Result: BREAKEVEN")
                            print(msg)
                            send_telegram_fn(msg)
                            has_changes = True
                        elif low <= t2:
                            trade["status"] = "CLOSED"
                            trade["exit_date"] = date_str
                            pnl = (entry_p - t2) * (qty / 2)
                            log_closed_trade(ticker, direction, entry_p, initial_sl, t1, t2, t2, pnl, "PROFIT", "HIT_TARGET_2", duration, trade.get("trade_type", "POSITIONAL"), trade.get("strategy", "NR7"))
                            msg = (f"[INFO] TARGET 2 HIT (FULL EXIT)\n"
                                   f"Stock: #{ticker}\n"
                                   f"Exit Price: `{t2:.2f}` (T2)\n"
                                   f"PnL Booked: `Rs.{pnl:.2f}`\n"
                                   f"Result: PROFIT")
                            print(msg)
                            send_telegram_fn(msg)
                            has_changes = True
            
            updated_trades.append(trade)
        except Exception as e:
            print(f"Error managing trade for {ticker}: {e}")
            updated_trades.append(trade)
            
    if has_changes:
        save_paper_trades(updated_trades)
