import pandas as pd
import yfinance as yf
import datetime
import pytz
import os

from db_connector import db

def get_coll(ticker: str):
    """Dynamically get or create a symbol-wise collection with indexes."""
    if db is None:
        return None
    safe_t = ticker.replace('.', '_').replace('^', '')
    coll = db[f"ohlcv_{safe_t}"]
    try:
        coll.create_index([("interval", 1), ("timestamp", -1)], unique=True)
    except Exception:
        pass
    return coll

def parse_period_to_cutoff(period: str):
    """Converts a yfinance period string to a datetime cutoff."""
    now = datetime.datetime.now(pytz.UTC)
    if period.endswith('d'):
        num = int(period[:-1])
        return now - datetime.timedelta(days=num * 2) # Extra buffer for weekends/holidays
    elif period.endswith('mo'):
        num = int(period[:-2])
        return now - datetime.timedelta(days=num * 32)
    elif period.endswith('y'):
        num = int(period[:-1])
        return now - datetime.timedelta(days=num * 366)
    elif period.endswith('wk'):
        num = int(period[:-2])
        return now - datetime.timedelta(weeks=num + 1)
    
    return now - datetime.timedelta(days=365) # Fallback

def get_cached_yfinance(tickers, period="1y", interval="1d", group_by="column", progress=False, auto_adjust=True):
    """
    Drop-in replacement for yf.download() that persists data to MongoDB when available.
    If MongoDB is not running, falls back cleanly to direct yf.download().
    """
    if db is None:
        try:
            return yf.download(tickers, period=period, interval=interval, group_by=group_by, progress=progress, auto_adjust=auto_adjust)
        except Exception as e:
            print(f"[db_cache] Direct fetch error: {e}")
            return pd.DataFrame()

    if isinstance(tickers, str):
        if ',' in tickers:
            ticker_list = [t.strip() for t in tickers.split(',')]
        else:
            ticker_list = [t.strip() for t in tickers.split()]
    else:
        ticker_list = list(tickers)
        
    if not ticker_list:
        return pd.DataFrame()

    cutoff_date = parse_period_to_cutoff(period)
    
    # 1. Determine delta requirement
    fetch_start = None
    
    for t in ticker_list:
        coll = get_coll(t)
        if coll is None:
            fetch_start = cutoff_date
            break
        latest_doc = coll.find_one({"interval": interval}, sort=[("timestamp", -1)])
        
        if latest_doc:
            ld = latest_doc["timestamp"]
            if ld.tzinfo is None:
                ld = pytz.UTC.localize(ld)
            if ld < cutoff_date:
                fetch_start = cutoff_date
                break
            else:
                if fetch_start is None or ld < fetch_start:
                    fetch_start = ld
        else:
            fetch_start = cutoff_date
            break
            
    # 2. Fetch missing delta via yfinance
    if fetch_start:
        dl_start = (fetch_start - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        try:
            downloaded = yf.download(ticker_list, start=dl_start, interval=interval, group_by="ticker", progress=progress, auto_adjust=auto_adjust)
        except Exception as e:
            print(f"[MongoCache] Delta fetch error for {dl_start}: {e}")
            downloaded = pd.DataFrame()
            
        if not downloaded.empty:
            docs = []
            
            if len(ticker_list) == 1:
                df_t = downloaded
                if "Close" in df_t.columns:
                    df_t = df_t.dropna(subset=['Close'])
                for index, row in df_t.iterrows():
                    if pd.isna(row.get("Close")): continue
                    ts = index.to_pydatetime()
                    if ts.tzinfo is None:
                        ts = pytz.UTC.localize(ts)
                    docs.append({
                        "ticker": ticker_list[0],
                        "interval": interval,
                        "timestamp": ts,
                        "Open": float(row["Open"]),
                        "High": float(row["High"]),
                        "Low": float(row["Low"]),
                        "Close": float(row["Close"]),
                        "Volume": float(row.get("Volume", 0.0))
                    })
            else:
                for t in ticker_list:
                    if t in downloaded.columns.levels[0]:
                        df_t = downloaded[t]
                        if "Close" in df_t.columns:
                            df_t = df_t.dropna(subset=['Close'])
                        for index, row in df_t.iterrows():
                            if pd.isna(row.get("Close")): continue
                            ts = index.to_pydatetime()
                            if ts.tzinfo is None:
                                ts = pytz.UTC.localize(ts)
                            docs.append({
                                "ticker": t,
                                "interval": interval,
                                "timestamp": ts,
                                "Open": float(row["Open"]),
                                "High": float(row["High"]),
                                "Low": float(row["Low"]),
                                "Close": float(row["Close"]),
                                "Volume": float(row.get("Volume", 0.0))
                            })
                            
            if docs:
                try:
                    from pymongo import UpdateOne
                    docs_by_ticker = {}
                    for d in docs:
                        docs_by_ticker.setdefault(d["ticker"], []).append(d)
                        
                    for t_sym, t_docs in docs_by_ticker.items():
                        coll = get_coll(t_sym)
                        if coll is None: continue
                        operations = [
                            UpdateOne(
                                {"interval": d["interval"], "timestamp": d["timestamp"]},
                                {"$set": d},
                                upsert=True
                            ) for d in t_docs
                        ]
                        if operations:
                            coll.bulk_write(operations, ordered=False)
                except Exception as e:
                    print(f"[MongoCache] Bulk write error: {e}")

    # 3. Assemble and serve DataFrame from local MongoDB
    frames = {}
    for t in ticker_list:
        coll = get_coll(t)
        if coll is None:
            frames[t] = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            continue
        cursor = coll.find({"interval": interval, "timestamp": {"$gte": cutoff_date}}).sort("timestamp", 1)
        data = list(cursor)
        
        if data:
            df_t = pd.DataFrame(data)
            df_t.set_index("timestamp", inplace=True)
            df_t = df_t[["Open", "High", "Low", "Close", "Volume"]]
            df_t.index.name = "Date" if interval.endswith('d') or interval.endswith('wk') or interval.endswith('mo') else "Datetime"
            frames[t] = df_t
        else:
            frames[t] = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            
    if len(ticker_list) == 1:
        return frames[ticker_list[0]]
    else:
        df_concat = pd.concat(frames, axis=1)
        if df_concat.empty:
            return df_concat
            
        if group_by == "ticker":
            return df_concat
        else:
            df_concat.columns = df_concat.columns.swaplevel(0, 1)
            df_concat.sort_index(axis=1, level=0, inplace=True)
            return df_concat
