import yfinance as yf
import pandas as pd
import numpy as np
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(BASE_DIR, "src"))
from scanner import core

BACKTEST_TICKERS = core.get_dynamic_tickers()

def get_nifty_regime_dict():
    nifty_df = yf.download("^NSEI", period="2y", interval="1d", progress=False, auto_adjust=True)
    if isinstance(nifty_df.columns, pd.MultiIndex):
        nifty_df.columns = nifty_df.columns.get_level_values(0)
    nifty_df['SMA_50'] = nifty_df['Close'].rolling(50).mean()
    
    regime = {}
    for date, row in nifty_df.iterrows():
        d_str = date.strftime('%Y-%m-%d')
        if pd.isna(row['SMA_50']):
            regime[d_str] = "NEUTRAL"
        elif float(row['Close']) > float(row['SMA_50']):
            regime[d_str] = "BULLISH"
        else:
            regime[d_str] = "BEARISH"
    return regime

def backtest_single_ticker(ticker, nifty_regime):
    """Backtests the NR7 Inside Bar strategy on a single ticker over the last 1 year."""
    trades = []
    try:
        df = yf.download(ticker + ".NS", period="2y", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 100:
            return []
            
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        df['Range'] = df['High'] - df['Low']
        df['SMA_50'] = df['Close'].rolling(50).mean()
        df['SMA_10'] = df['Close'].rolling(10).mean()
        df['SMA_200'] = df['Close'].rolling(200).mean()
        df['Vol_SMA_20'] = df['Volume'].rolling(20).mean()
        
        # RSI 14
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI_14'] = 100 - (100 / (1 + rs))
        
        df['Prev_Close'] = df['Close'].shift(1)
        df['TR'] = np.maximum(
            df['High'] - df['Low'],
            np.maximum(
                abs(df['High'] - df['Prev_Close']),
                abs(df['Low'] - df['Prev_Close'])
            )
        )
        df['ATR'] = df['TR'].rolling(14).mean()
        
        # Identify setups
        for i in range(50, len(df) - 3):
            # Check setup on index i
            sub = df.iloc[i-7:i+1]
            if len(sub) < 8:
                continue
                
            # NR7 check
            ranges = df['Range'].iloc[i-6:i+1].tolist()
            is_nr7 = ranges[-1] == min(ranges)
            
            # Use NR7 for high probability
            if is_nr7:
                latest = df.iloc[i]
                setup_high = float(latest['High'])
                setup_low = float(latest['Low'])
                setup_close = float(latest['Close'])
                setup_date = df.index[i].strftime('%Y-%m-%d')
                
                # Determine Trend
                sma50 = float(latest['SMA_50'])
                sma10 = float(latest['SMA_10'])
                sma200 = float(latest['SMA_200'])
                rsi = float(latest['RSI_14'])
                vol = float(latest['Volume'])
                vol_sma = float(latest['Vol_SMA_20'])
                
                # Check for volatility contraction AND momentum
                # Check for volatility contraction AND momentum
                is_bullish = (setup_close > sma50) and (sma10 > sma50) and (rsi > 55)
                is_bearish = (setup_close < sma50) and (sma10 < sma50) and (rsi < 45)
                
                regime = nifty_regime.get(setup_date, "NEUTRAL")
                
                if is_bullish and regime == "BULLISH":
                    direction = "BUY"
                elif is_bearish and regime == "BEARISH":
                    direction = "SELL"
                else:
                    continue # Block trade due to market regime mismatch or no clear trend
                    
                # Look for trigger in next 3 days
                triggered = False
                trigger_idx = -1
                for j in range(i+1, min(i+4, len(df))):
                    day_j = df.iloc[j]
                    if direction == "BUY":
                        if float(day_j['High']) > setup_high:
                            triggered = True
                            trigger_idx = j
                            break
                    else:
                        if float(day_j['Low']) < setup_low:
                            triggered = True
                            trigger_idx = j
                            break
                            
                if triggered:
                    # Execute trade simulation
                    atr = float(latest['ATR'])
                    if pd.isna(atr) or atr <= 0:
                        continue
                        
                    entry = setup_high + 0.10 if direction == "BUY" else setup_low - 0.10
                    sl = entry - (1.5 * atr) if direction == "BUY" else entry + (1.5 * atr)
                    risk = abs(entry - sl)
                    
                    # Intraday vs Positional Logic
                    close_price = float(latest['Close'])
                    atr_pct = (atr / close_price) * 100
                    rsi = float(latest['RSI_14'])
                    
                    is_intraday = False
                    if atr_pct > 3.5:
                        is_intraday = True
                    if direction == "BUY" and rsi > 70:
                        is_intraday = True
                    if direction == "SELL" and rsi < 30:
                        is_intraday = True
                        
                    t1_mult = 1.0 if is_intraday else 1.5
                    
                    # Target 1 mapped to ATR
                    target1 = entry + (t1_mult * atr) if direction == "BUY" else entry - (t1_mult * atr)
                    
                    # Track trade outcome until SL or Target 1 is hit
                    outcome = "PENDING"
                    exit_price = 0
                    exit_date = None
                    
                    for k in range(trigger_idx, len(df)):
                        day_k = df.iloc[k]
                        if direction == "BUY":
                            # Check Target 1 first
                            if day_k['High'] >= target1:
                                outcome = "PROFIT"
                                exit_price = target1
                                exit_date = df.index[k]
                                break
                            # Check SL
                            if day_k['Low'] <= sl:
                                outcome = "LOSS"
                                exit_price = sl
                                exit_date = df.index[k]
                                break
                        else:
                            # Check Target 1
                            if day_k['Low'] <= target1:
                                outcome = "PROFIT"
                                exit_price = target1
                                exit_date = df.index[k]
                                break
                            # Check SL
                            if day_k['High'] >= sl:
                                outcome = "LOSS"
                                exit_price = sl
                                exit_date = df.index[k]
                                break
                                
                        # Intraday Time Stop: Exit at end of trigger day if not hit
                        if is_intraday and k == trigger_idx:
                            # We didn't hit profit or loss intraday, force exit on close
                            outcome = "LOSS" if (direction == "BUY" and float(day_k['Close']) < entry) or (direction == "SELL" and float(day_k['Close']) > entry) else "PROFIT"
                            exit_price = float(day_k['Close'])
                            exit_date = df.index[k]
                            break
                                
                        # Intraday Time Stop: Exit at end of trigger day if not hit
                        if is_intraday and k == trigger_idx:
                            # We didn't hit profit or loss intraday, force exit on close
                            outcome = "LOSS" if (direction == "BUY" and float(day_k['Close']) < entry) or (direction == "SELL" and float(day_k['Close']) > entry) else "PROFIT"
                            exit_price = float(day_k['Close'])
                            exit_date = df.index[k]
                            break
                                
                    if outcome != "PENDING":
                        pnl = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
                        pnl_pct = (pnl / entry) * 100
                        trades.append({
                            "ticker": ticker,
                            "date": df.index[i].strftime("%Y-%m-%d"),
                            "direction": direction,
                            "entry": entry,
                            "sl": sl,
                            "target1": target1,
                            "exit_price": exit_price,
                            "pnl": pnl,
                            "pnl_pct": pnl_pct,
                            "outcome": outcome
                        })
    except Exception:
        pass
    return trades

def run_backtest():
    print("=========================================================")
    print(" [INFO] NR7 INSIDE BAR BACKTESTER (1-YEAR HISTORICAL DATA)")
    print(f" Backtesting {len(BACKTEST_TICKERS)} liquid F&O stocks...")
    print(" [INFO] FETCHING MACRO REGIME DATA (NIFTY 50)...")
    nifty_regime = get_nifty_regime_dict()
    print("=========================================================\n")
    
    all_trades = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(backtest_single_ticker, ticker, nifty_regime): ticker for ticker in BACKTEST_TICKERS}
        for future in as_completed(futures):
            res = future.result()
            if res:
                all_trades.extend(res)
                
    if not all_trades:
        print("No trades triggered during the backtest period.")
        return
        
    df_trades = pd.DataFrame(all_trades)
    df_trades = df_trades.sort_values(by='date', ascending=False)
    
    csv_path = os.path.join(BASE_DIR, "data", "backtest_recent.csv")
    df_trades.head(50).to_csv(csv_path, index=False)
    print(f"Saved recent 50 trades to {csv_path}")
    
    # Calculate performance metrics
    total_trades = len(df_trades)
    winning_trades = len(df_trades[df_trades['outcome'] == 'PROFIT'])
    losing_trades = len(df_trades[df_trades['outcome'] == 'LOSS'])
    win_rate = (winning_trades / total_trades) * 100
    
    total_profit_pts = df_trades[df_trades['pnl'] > 0]['pnl'].sum()
    total_loss_pts = abs(df_trades[df_trades['pnl'] < 0]['pnl'].sum())
    profit_factor = total_profit_pts / total_loss_pts if total_loss_pts > 0 else float('inf')
    
    avg_pnl_pct = df_trades['pnl_pct'].mean()
    
    print(" [INFO] BACKTEST PERFORMANCE SUMMARY:")
    print("-----------------------------------------")
    print(f"  Total Trades Triggered : {total_trades}")
    print(f"  Winning Trades         : {winning_trades} (Win Rate: {win_rate:.2f}%)")
    print(f"  Losing Trades          : {losing_trades}")
    print(f"  Profit Factor          : {profit_factor:.2f}")
    print(f"  Avg Trade PnL %        : {avg_pnl_pct:.2f}%")
    print("-----------------------------------------")
    
    # Breakdown by Direction
    for d in ["BUY", "SELL"]:
        df_d = df_trades[df_trades['direction'] == d]
        if not df_d.empty:
            d_total = len(df_d)
            d_wins = len(df_d[df_d['outcome'] == 'PROFIT'])
            d_win_rate = (d_wins / d_total) * 100
            print(f"  {d} (Longs/Shorts) Setup Trades: {d_total} | Win Rate: {d_win_rate:.2f}%")
            
    print("=========================================")

if __name__ == "__main__":
    run_backtest()
