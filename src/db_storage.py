import os
import json
import csv
import datetime
from db_connector import db

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
os.makedirs(DATA_DIR, exist_ok=True)

WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.txt")
PAPER_TRADES_FILE = os.path.join(DATA_DIR, "paper_trades.json")
PNL_LOG_FILE = os.path.join(DATA_DIR, "pnl_log.csv")

# MongoDB collections (if MongoDB is available and running)
watch_coll = db['watchlist'] if db is not None else None
paper_coll = db['paper_trades'] if db is not None else None
pnl_coll = db['pnl_log'] if db is not None else None

def get_watchlist():
    if watch_coll is not None:
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
            res.append(f"{p1} | {p2} | {p3} | {p4} | {p5} | {p6} | {p7} | {p8}")
        return res

    # File-based fallback
    if not os.path.exists(WATCHLIST_FILE):
        return []
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

def clear_watchlist():
    if watch_coll is not None:
        watch_coll.delete_many({})
        return

    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        f.write("")

def append_to_watchlist(entry_str):
    parts = entry_str.split('|')
    ticker = parts[0].strip() if len(parts) > 0 else ""
    if not ticker:
        return

    if watch_coll is not None:
        watch_coll.delete_one({"ticker": ticker})
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
        return

    # File-based fallback
    existing = []
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip() and line.split('|')[0].strip() != ticker:
                    existing.append(line.strip())
    existing.append(entry_str.strip())
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(existing) + "\n")

def remove_from_watchlist(ticker):
    if watch_coll is not None:
        watch_coll.delete_one({"ticker": ticker})
        return

    if not os.path.exists(WATCHLIST_FILE):
        return
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and line.split('|')[0].strip() != ticker]
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))

def get_paper_trades():
    if paper_coll is not None:
        return list(paper_coll.find({}, {'_id': False}))

    if not os.path.exists(PAPER_TRADES_FILE):
        return []
    try:
        with open(PAPER_TRADES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_paper_trades(trades):
    if paper_coll is not None:
        paper_coll.delete_many({})
        if trades:
            paper_coll.insert_many(trades)
        return

    with open(PAPER_TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=4)

def log_closed_trade(ticker, direction, entry, sl, target1, target2, exit_price, pnl, result, reason, duration_days, trade_type="POSITIONAL", strategy="NR7"):
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    record = {
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
    }

    if pnl_coll is not None:
        pnl_coll.insert_one(record)
        return

    # File-based CSV fallback
    fieldnames = list(record.keys())
    file_exists = os.path.exists(PNL_LOG_FILE) and os.path.getsize(PNL_LOG_FILE) > 0
    with open(PNL_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(record)

def get_pnl_log():
    if pnl_coll is not None:
        return list(pnl_coll.find({}, {'_id': False}))

    if not os.path.exists(PNL_LOG_FILE):
        return []
    try:
        with open(PNL_LOG_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)
    except Exception:
        return []
