from pymongo import MongoClient
import datetime
from db_connector import db

# Collections
watch_coll = db['watchlist']
paper_coll = db['paper_trades']
pnl_coll = db['pnl_log']

def get_watchlist():
    docs = watch_coll.find().sort('_id', 1)
    res = []
    for d in docs:
        p1 = d.get('ticker', '')
        p2 = d.get('strategy', '')
        p3 = d.get('direction', '')
        p4 = d.get('trade_type', 'POSITIONAL')
        p5 = d.get('entry_price', '')
        p6 = d.get('projection', 'PROJ_NONE_0.0')
        p7 = d.get('target1', '')
        p8 = d.get('target2', '')
        # Formulate string compatible with downstream parsing
        res.append(f"{p1} | {p2} | {p3} | {p4} | {p5} | {p6} | {p7} | {p8}")
    return res

def clear_watchlist():
    watch_coll.delete_many({})

def append_to_watchlist(entry_str):
    parts = entry_str.split('|')
    ticker = parts[0].strip() if len(parts) > 0 else ""
    if not ticker: return
    
    # Remove existing
    watch_coll.delete_one({"ticker": ticker})
    
    # Insert new
    watch_coll.insert_one({
        "ticker": ticker,
        "strategy": parts[1].strip() if len(parts) > 1 else "",
        "direction": parts[2].strip() if len(parts) > 2 else "",
        "trade_type": parts[3].strip() if len(parts) > 3 else "POSITIONAL",
        "entry_price": parts[4].strip() if len(parts) > 4 else "",
        "projection": parts[5].strip() if len(parts) > 5 else "PROJ_NONE_0.0",
        "target1": parts[6].strip() if len(parts) > 6 else "",
        "target2": parts[7].strip() if len(parts) > 7 else ""
    })

def remove_from_watchlist(ticker):
    watch_coll.delete_one({"ticker": ticker})

def get_paper_trades():
    return list(paper_coll.find({}, {'_id': False}))
    
def save_paper_trades(trades):
    paper_coll.delete_many({})
    if trades:
        paper_coll.insert_many(trades)

def log_closed_trade(ticker, direction, entry, sl, target1, target2, exit_price, pnl, result, reason, duration_days, trade_type="POSITIONAL", strategy="NR7"):
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    pnl_coll.insert_one({
        "date": date_str,
        "ticker": ticker,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "target1": target1,
        "target2": target2,
        "exit_price": exit_price,
        "pnl": pnl,
        "result": result,
        "reason": reason,
        "duration_days": duration_days,
        "trade_type": trade_type,
        "strategy": strategy
    })
    
def get_pnl_log():
    return list(pnl_coll.find({}, {'_id': False}))
