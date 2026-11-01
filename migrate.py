import os
import sys
import json
import csv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(BASE_DIR, "src"))

from db_connector import db

print("Starting MongoDB migration...")

wl_file = os.path.join(BASE_DIR, "data", "watchlist.txt")
if os.path.exists(wl_file):
    with open(wl_file, "r") as f:
        for line in f:
            l = line.strip()
            if not l: continue
            parts = [p.strip() for p in l.split("|")]
            ticker = parts[0]
            db.watchlist.update_one(
                {"ticker": ticker},
                {"$set": {
                    "ticker": ticker,
                    "strategy": parts[1] if len(parts)>1 else "",
                    "direction": parts[2] if len(parts)>2 else "",
                    "trade_type": parts[3] if len(parts)>3 else "POSITIONAL",
                    "entry_price": parts[4] if len(parts)>4 else "",
                    "projection": parts[5] if len(parts)>5 else "PROJ_NONE_0.0",
                    "target1": parts[6] if len(parts)>6 else "",
                    "target2": parts[7] if len(parts)>7 else ""
                }},
                upsert=True
            )
    print("Watchlist migrated.")

pt_file = os.path.join(BASE_DIR, "data", "paper_trades.json")
if os.path.exists(pt_file):
    try:
        with open(pt_file, "r") as f:
            content = f.read().strip()
            if content:
                trades = json.loads(content)
                for t in trades:
                    db.paper_trades.update_one(
                        {"ticker": t["ticker"], "entry_date": t.get("entry_date", t.get("setup_date"))},
                        {"$set": t},
                        upsert=True
                    )
        print("Paper trades migrated.")
    except Exception as e:
        print(f"Error migrating paper trades: {e}")

pl_file = os.path.join(BASE_DIR, "data", "pnl_log.csv")
if os.path.exists(pl_file):
    try:
        with open(pl_file, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                db.pnl_log.update_one(
                    {"date": row["date"], "ticker": row["ticker"], "direction": row["direction"]},
                    {"$set": dict(row)},
                    upsert=True
                )
        print("PnL log migrated.")
    except Exception as e:
        print(f"Error migrating pnl log: {e}")

print("Migration complete!")
