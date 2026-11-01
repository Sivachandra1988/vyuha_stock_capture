import os
import sys
import pandas as pd
import yfinance as yf
from datetime import datetime
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.join(BASE_DIR, "src"))

from scanner import core
from scanner.advanced_trend import check_advanced_trend_for_dataframe

def run_backtest():
    print("--- Starting Advanced Trend 15m Backtest ---")
    tickers = core.get_dynamic_tickers()
    if not tickers:
        print("No tickers found. Falling back to Nifty 50.")
        tickers = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]
    
    # Take top 30 to make the backtest reasonably fast but significant
    tickers = tickers[:30]
    ticker_ns_list = [t + ".NS" for t in tickers]
    
    print(f"Fetching 60 days of 15m data for {len(tickers)} tickers...")
    try:
        df_all = yf.download(ticker_ns_list, period="60d", interval="15m", group_by="ticker", auto_adjust=True, progress=True)
        print("Fetching daily data for trend context...")
        df_daily_all = yf.download(ticker_ns_list, period="100d", interval="1d", group_by="ticker", auto_adjust=True, progress=True)
    except Exception as e:
        print(f"Error fetching data: {e}")
        return
        
    trades_taken = 0
    wins = 0
    losses = 0
    total_pnl = 0.0
    failure_log = []
    
    for ticker in tickers:
        ticker_ns = ticker + ".NS"
        if ticker_ns not in df_all.columns.levels[0]:
            continue
            
        df = df_all[ticker_ns].dropna()
        if len(df) < 100:
            continue
            
        df_d = df_daily_all[ticker_ns].dropna()
        if not df_d.empty:
            df_d = df_d.copy()
            df_d['EMA_10'] = df_d['Close'].ewm(span=10, adjust=False).mean()
            df_d['EMA_20'] = df_d['Close'].ewm(span=20, adjust=False).mean()
            delta = df_d['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df_d['RSI_14'] = 100 - (100 / (1 + rs))
            
        print(f"Processing {ticker} ({len(df)} candles)...")
        active_trade = None
        
        # Iterate through the dataframe chronologically, starting from index 50 to allow indicators to build
        for i in range(50, len(df)):
            current_bar = df.iloc[i]
            current_time = current_bar.name
            
            # 1. Manage Active Trade
            if active_trade:
                high = float(current_bar['High'])
                low = float(current_bar['Low'])
                close = float(current_bar['Close'])
                
                # Check for EOD Square Off (3:15 PM)
                if current_time.hour == 15 and current_time.minute >= 15:
                    # Check Daily Trend for carry-over
                    carry_over = False
                    if not df_d.empty:
                        # get daily data up to today
                        historical_daily = df_d[df_d.index.date <= current_time.date()]
                        if not historical_daily.empty:
                            latest_d = historical_daily.iloc[-1]
                            c_ema10 = float(latest_d['EMA_10'])
                            c_ema20 = float(latest_d['EMA_20'])
                            c_rsi = float(latest_d['RSI_14'])
                            if active_trade['direction'] == "BUY":
                                if c_ema10 > c_ema20 and c_rsi > 50:
                                    carry_over = True
                            else:
                                if c_ema10 < c_ema20 and c_rsi < 50:
                                    carry_over = True
                    
                    if carry_over:
                        # Skip square-off, continue to hold
                        continue
                        
                    # Force Close
                    pnl = (close - active_trade['entry']) if active_trade['direction'] == "BUY" else (active_trade['entry'] - close)
                    if pnl > 0:
                        wins += 1
                    else:
                        losses += 1
                        failure_log.append({
                            "ticker": ticker,
                            "type": "EOD Square-off Loss",
                            "entry": active_trade['entry'],
                            "close": close,
                            "date": current_time.strftime("%Y-%m-%d %H:%M"),
                            "pnl": pnl
                        })
                    total_pnl += pnl
                    active_trade = None
                    continue
                
                # Check Triggers
                if active_trade['direction'] == "BUY":
                    if low <= active_trade['sl']:
                        losses += 1
                        pnl = (active_trade['sl'] - active_trade['entry'])
                        total_pnl += pnl
                        failure_log.append({
                            "ticker": ticker,
                            "type": "SL Hit (Long)",
                            "entry": active_trade['entry'],
                            "sl": active_trade['sl'],
                            "low": low,
                            "date": current_time.strftime("%Y-%m-%d %H:%M"),
                            "pnl": pnl
                        })
                        active_trade = None
                    elif high >= active_trade['target2']:
                        wins += 1
                        total_pnl += (active_trade['target2'] - active_trade['entry'])
                        active_trade = None
                    elif high >= active_trade['target1'] and not active_trade.get('t1_hit'):
                        active_trade['t1_hit'] = True
                        active_trade['sl'] = active_trade['entry'] # Trail to breakeven
                else: # SELL
                    if high >= active_trade['sl']:
                        losses += 1
                        pnl = (active_trade['entry'] - active_trade['sl'])
                        total_pnl += pnl
                        failure_log.append({
                            "ticker": ticker,
                            "type": "SL Hit (Short)",
                            "entry": active_trade['entry'],
                            "sl": active_trade['sl'],
                            "high": high,
                            "date": current_time.strftime("%Y-%m-%d %H:%M"),
                            "pnl": pnl
                        })
                        active_trade = None
                    elif low <= active_trade['target2']:
                        wins += 1
                        total_pnl += (active_trade['entry'] - active_trade['target2'])
                        active_trade = None
                    elif low <= active_trade['target1'] and not active_trade.get('t1_hit'):
                        active_trade['t1_hit'] = True
                        active_trade['sl'] = active_trade['entry'] # Trail to breakeven
                        
                continue # Wait for trade to resolve before looking for new signals
            
            # 2. Look for new signals
            # Skip new entries in the last hour of the day to avoid overnight risk on intraday strategy
            if current_time.hour >= 14:
                continue
                
            df_slice = df.iloc[:i+1]
            signal = check_advanced_trend_for_dataframe(ticker, df_slice)
            
            if signal:
                active_trade = signal
                trades_taken += 1
                
    print("\n--- BACKTEST RESULTS (60 Days) ---")
    print(f"Total Tickers Scanned: {len(tickers)}")
    print(f"Total Trades Taken: {trades_taken}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    
    if trades_taken > 0:
        win_rate = (wins / trades_taken) * 100
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Net Points PnL (unweighted): {total_pnl:.2f}")
        
        print("\n--- FAILURE LOG ANALYSIS ---")
        for f in failure_log:
            print(f"[{f['date']}] {f['ticker']} | {f['type']} | Entry: {f['entry']:.2f} | PnL: {f['pnl']:.2f}")
    else:
        print("No trades triggered.")

if __name__ == "__main__":
    run_backtest()
