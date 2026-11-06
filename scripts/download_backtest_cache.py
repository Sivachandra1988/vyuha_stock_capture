"""
Downloads historical 2-year daily OHLCV data for liquid F&O universe
and caches them into data/backtest_cache/{ticker}.parquet
"""
import os
import sys
import yfinance as yf
import pandas as pd
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
DATA_DIR = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "backtest_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from scanner import core

TICKERS = [
    # Nifty 50 High-Volume Leaders
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "BHARTIARTL",
    "ITC", "LT", "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA", "ADANIENT",
    "TATAMOTORS", "KOTAKBANK", "NTPC", "TITAN", "AXISBANK", "ONGC", "POWERGRID",
    "COALINDIA", "TATASTEEL", "BAJAJFINSV", "M&M", "ADANIPORTS", "ASIANPAINT",
    "JSWSTEEL", "ULTRACEMCO", "GRASIM", "NESTLEIND", "WIPRO", "TECHM",

    # High-Beta Swing & Momentum Leaders
    "SHRIRAMFIN", "INDIGO", "EICHERMOT", "HINDALCO", "BEL", "TMPV", "JIOFIN",
    "DRREDDY", "LAURUSLABS", "ADANIPOWER", "VBL", "CUMMINSIND", "BSE", "MCX",
    "MAXHEALTH", "TATACONSUM", "APOLLOHOSP", "DLF", "HAL", "VEDL", "POLYCAB"
]

def fetch_and_save_ticker(ticker):
    parquet_path = os.path.join(CACHE_DIR, f"{ticker}.parquet")
    if os.path.exists(parquet_path) and os.path.getsize(parquet_path) > 1000:
        return ticker, True, "Cached"
    try:
        df = yf.download(f"{ticker}.NS", period="2y", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 50:
            return ticker, False, "Empty DataFrame"
        
        # Flatten MultiIndex
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        
        df.to_parquet(parquet_path)
        return ticker, True, f"Downloaded {len(df)} bars"
    except Exception as e:
        return ticker, False, str(e)

def main():
    print(f"Starting historical data download for {len(TICKERS)} liquid F&O stocks...")
    success_count = 0
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = executor.map(fetch_and_save_ticker, TICKERS)
        for ticker, status, msg in results:
            status_icon = "✓" if status else "✗"
            print(f"  {status_icon} {ticker:<12}: {msg}")
            if status:
                success_count += 1

    print(f"\nDone! Successfully cached {success_count}/{len(TICKERS)} stocks into {CACHE_DIR}")

if __name__ == "__main__":
    main()
