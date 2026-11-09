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
DAILY_RECOMMENDATIONS_FILE = os.path.join(DATA_DIR, "daily_recommendations.json")

# MongoDB collections (if MongoDB is available and running)
watch_coll = db['watchlist'] if db is not None else None
paper_coll = db['paper_trades'] if db is not None else None
pnl_coll = db['pnl_log'] if db is not None else None
recs_coll = db['daily_recommendations'] if db is not None else None

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
            p7 = d.get('sl', '')
            p8 = d.get('target1', '')
            p9 = d.get('target2', '')
            res.append(f"{p1} | {p2} | {p3} | {p4} | {p5} | {p6} | {p7} | {p8} | {p9}")
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
    parts = [p.strip() for p in entry_str.split('|')]
    ticker = parts[0] if len(parts) > 0 else ""
    if not ticker:
        return

    strategy = parts[1] if len(parts) > 1 else ""
    direction = parts[2] if len(parts) > 2 else ""
    trade_type = parts[3] if len(parts) > 3 else "POSITIONAL"
    entry_price = parts[4] if len(parts) > 4 else ""
    projection = parts[5] if len(parts) > 5 else "PROJ_NONE_0.0"

    sl = ""
    target1 = ""
    target2 = ""

    if len(parts) >= 9:
        sl = parts[6]
        target1 = parts[7]
        target2 = parts[8]
    elif len(parts) == 8:
        # Detect if parts[6] is SL or target1 by checking relation to entry_price
        try:
            e_val = float(entry_price)
            p6_val = float(parts[6])
            p7_val = float(parts[7])
            is_short = 'SHORT' in direction.upper() or 'SELL' in direction.upper()
            if not is_short:
                if p6_val < e_val and p7_val > e_val:
                    sl = parts[6]
                    target1 = parts[7]
                else:
                    target1 = parts[6]
                    target2 = parts[7]
            else:
                if p6_val > e_val and p7_val < e_val:
                    sl = parts[6]
                    target1 = parts[7]
                else:
                    target1 = parts[6]
                    target2 = parts[7]
        except Exception:
            target1 = parts[6]
            target2 = parts[7]
    elif len(parts) == 7:
        target1 = parts[6]

    if watch_coll is not None:
        watch_coll.delete_one({"ticker": ticker})
        watch_coll.insert_one({
            "ticker": ticker,
            "strategy": strategy,
            "direction": direction,
            "trade_type": trade_type,
            "entry_price": entry_price,
            "projection": projection,
            "sl": sl,
            "target1": target1,
            "target2": target2
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
    trades = []
    if paper_coll is not None:
        try:
            trades = list(paper_coll.find({}, {'_id': False}))
        except Exception:
            trades = []

    if not trades and os.path.exists(PAPER_TRADES_FILE):
        try:
            with open(PAPER_TRADES_FILE, "r", encoding="utf-8") as f:
                trades = json.load(f)
        except Exception:
            trades = []
    return trades

def save_paper_trades(trades):
    # Create clean copies without MongoDB _id for JSON serialization
    clean_trades = []
    for t in trades:
        t_copy = dict(t)
        t_copy.pop('_id', None)
        clean_trades.append(t_copy)

    if paper_coll is not None:
        try:
            paper_coll.delete_many({})
            if clean_trades:
                paper_coll.insert_many([dict(t) for t in clean_trades])
        except Exception as e:
            print(f"Error syncing paper trades to MongoDB: {e}")

    try:
        with open(PAPER_TRADES_FILE, "w", encoding="utf-8") as f:
            json.dump(clean_trades, f, indent=4)
    except Exception as e:
        print(f"Error saving paper trades to file: {e}")

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

def is_ticker_in_cooldown_today(ticker, date_str=None):
    """
    Returns True if the ticker has hit Stop-Loss or been closed with a loss today.
    Used to prevent same-day whipsaw flips and revenge trading.
    """
    today_str = date_str or datetime.datetime.now().strftime("%Y-%m-%d")
    if pnl_coll is not None:
        try:
            match = pnl_coll.find_one({
                "ticker": ticker,
                "date": today_str,
                "reason": {"$in": ["HIT_SL", "HIT_SL_BREAKEVEN", "STOP LOSS"]}
            })
            if match:
                return True
        except Exception:
            pass

    # File-based check
    if os.path.exists(PNL_LOG_FILE):
        try:
            with open(PNL_LOG_FILE, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("ticker") == ticker and row.get("date") == today_str:
                        if row.get("reason") in ["HIT_SL", "HIT_SL_BREAKEVEN", "STOP LOSS"]:
                            return True
        except Exception:
            pass
    return False

def record_daily_recommendation(ticker, strategy, direction, trade_type, entry, sl=0.0, target1=0.0, target2=0.0, reason="", date_str=None, option_info=None, **kwargs):
    """Records a fresh recommendation into today's daily recommendations list and syncs with MongoDB."""
    today_str = date_str or datetime.datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.datetime.now().strftime("%H:%M:%S")
    data = {"date": today_str, "items": []}

    if os.path.exists(DAILY_RECOMMENDATIONS_FILE):
        try:
            with open(DAILY_RECOMMENDATIONS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if loaded.get("date") == today_str:
                    data = loaded
        except Exception:
            pass

    # Check if already in today's items
    existing_idx = None
    for idx, item in enumerate(data.get("items", [])):
        if item.get("ticker") == ticker:
            existing_idx = idx
            break

    item_obj = {
        "ticker": ticker,
        "strategy": strategy,
        "direction": direction,
        "trade_type": trade_type,
        "entry_price": float(entry) if entry else 0.0,
        "sl": float(sl) if sl else 0.0,
        "target1": float(target1) if target1 else 0.0,
        "target2": float(target2) if target2 else 0.0,
        "reason": reason,
        "time": now_time,
        "date": today_str
    }

    if option_info and isinstance(option_info, dict):
        item_obj["option"] = {
            "contract": option_info.get("full_contract", option_info.get("contract", "")),
            "action": option_info.get("action", ""),
            "strike": option_info.get("strike", 0.0),
            "expiry": option_info.get("expiry", ""),
            "lot_size": option_info.get("lot_size", 0),
            "est_gain_t1_pts": option_info.get("est_gain_t1_pts", 0.0),
            "est_sl_pts": option_info.get("est_sl_pts", 0.0),
            "total_profit_t1_rs": option_info.get("total_profit_t1_rs", 0.0),
            "total_risk_rs": option_info.get("total_risk_rs", 0.0),
            "alt_strategy": option_info.get("alt_option_selling", "")
        }

    for k, v in kwargs.items():
        if k not in item_obj:
            item_obj[k] = v

    if existing_idx is not None:
        data["items"][existing_idx] = item_obj
    else:
        data["items"].append(item_obj)

    try:
        with open(DAILY_RECOMMENDATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving daily recommendations: {e}")

    # Sync to MongoDB collection
    if recs_coll is not None:
        try:
            recs_coll.update_one(
                {"ticker": ticker, "date": today_str},
                {"$set": item_obj},
                upsert=True
            )
        except Exception as e:
            print(f"Error syncing daily recommendation to MongoDB: {e}")

def get_today_recommendations():
    """Returns today's fresh recommendations list. Falls back to MongoDB or latest recorded recommendations."""
    if os.path.exists(DAILY_RECOMMENDATIONS_FILE):
        try:
            with open(DAILY_RECOMMENDATIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                items = data.get("items", [])
                if items:
                    return items
        except Exception:
            pass

    if recs_coll is not None:
        try:
            docs = list(recs_coll.find({}, {'_id': False}))
            if docs:
                return docs
        except Exception:
            pass

    return []

