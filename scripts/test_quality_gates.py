import os
import sys
import pandas as pd
import yfinance as yf

# Import current scripts
sys.path.extend(['.', 'src'])
from scanner import vyuha

def evaluate_daily_regime(ticker):
    try:
        df = yf.download(ticker + '.NS', period='3mo', interval='1d', progress=False, auto_adjust=True)
        if df.empty or len(df) < 20:
            return "UNKNOWN"
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        ema10 = df['Close'].ewm(span=10, adjust=False).mean()
        ema20 = df['Close'].ewm(span=20, adjust=False).mean()
        
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        c_ema10 = float(ema10.iloc[-1])
        c_ema20 = float(ema20.iloc[-1])
        c_rsi = float(rsi.iloc[-1])
        
        if c_ema10 > c_ema20 and c_rsi >= 50:
            return "BULLISH"
        elif c_ema10 < c_ema20 and c_rsi <= 50:
            return "BEARISH"
        else:
            return "NEUTRAL"
    except Exception as e:
        return "ERROR"

tickers = ['BHARTIARTL', 'WIPRO', 'ASIANPAINT', 'POWERGRID', 'TRENT', 'GRASIM', 'HCLTECH', 'HINDALCO', 'CUMMINSIND', 'AXISBANK', 'LAURUSLABS', 'SBIN']
print("=" * 60)
print(f"{'TICKER':12s} {'DAILY REGIME':15s} {'PROPOSED TODAY'}")
print("=" * 60)
for t in tickers:
    regime = evaluate_daily_regime(t)
    print(f"{t:12s} {regime:15s}")
