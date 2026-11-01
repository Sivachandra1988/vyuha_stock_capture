"""
mock_data.py — Inject realistic mock data for UI testing during weekends,
market holidays, and after-market hours.

Usage:
    python mock_data.py           # Inject full mock dataset
    python mock_data.py --reset   # Reset to empty state
"""

import json
import os
import sys
import random
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PAPER_TRADES_FILE = os.path.join(DATA_DIR, "paper_trades.json")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.txt")
PNL_LOG_FILE = os.path.join(DATA_DIR, "pnl_log.csv")

today = date.today().isoformat()

# ─── MOCK PAPER TRADES ────────────────────────────────────────────────────────
# Prices based on real yfinance close: TCS=2269, HDFCBANK=820, RELIANCE=1327,
#   INFY=1096, BAJFINANCE=1056, WIPRO=176, SBIN=1044, HCLTECH=1204
MOCK_TRADES = [
    # Active Long — Positional (TCS real ~2269, entered slightly below)
    {
        "ticker": "TCS",
        "status": "ACTIVE",
        "direction": "BUY",
        "entry_price": 2240.0,
        "quantity": 5,
        "target1": 2310.0,
        "target2": 2380.0,
        "initial_sl": 2205.0,
        "current_sl": 2225.0,
        "setup_date": today,
        "entry_date": today,
        "exit_date": None,
        "pnl": 0.0,
        "ltp": 2269.0,
        "trade_type": "POSITIONAL"
    },
    # Active Short — Intraday (HDFCBANK real ~820, entered slightly above)
    {
        "ticker": "HDFCBANK",
        "status": "ACTIVE",
        "direction": "SELL",
        "entry_price": 835.0,
        "quantity": 20,
        "target1": 808.0,
        "target2": 785.0,
        "initial_sl": 850.0,
        "current_sl": 850.0,
        "setup_date": today,
        "entry_date": today,
        "exit_date": None,
        "pnl": 0.0,
        "ltp": 819.6,
        "trade_type": "INTRADAY"
    },
    # Partial Exit — Positional Long (RELIANCE real ~1327)
    {
        "ticker": "RELIANCE",
        "status": "PARTIAL_EXIT",
        "direction": "BUY",
        "entry_price": 1300.0,
        "quantity": 8,
        "target1": 1350.0,
        "target2": 1400.0,
        "initial_sl": 1270.0,
        "current_sl": 1300.0,
        "setup_date": today,
        "entry_date": today,
        "exit_date": None,
        "pnl": 0.0,
        "ltp": 1327.2,
        "trade_type": "POSITIONAL"
    },
    # Pending Long — NR7 (INFY real ~1096, trigger slightly above)
    {
        "ticker": "INFY",
        "status": "PENDING",
        "direction": "BUY",
        "entry_price": 1105.0,
        "quantity": 12,
        "target1": 1140.0,
        "target2": 1175.0,
        "initial_sl": 1080.0,
        "setup_date": today,
        "entry_date": None,
        "exit_date": None,
        "pnl": 0.0,
        "trade_type": "POSITIONAL"
    },
    # Pending Short — Intraday (BAJFINANCE real ~1056, trigger slightly below)
    {
        "ticker": "BAJFINANCE",
        "status": "PENDING",
        "direction": "SELL",
        "entry_price": 1048.0,
        "quantity": 5,
        "target1": 1020.0,
        "target2": 995.0,
        "initial_sl": 1070.0,
        "setup_date": today,
        "entry_date": None,
        "exit_date": None,
        "pnl": 0.0,
        "trade_type": "INTRADAY"
    },
    # Closed winner (WIPRO real ~176)
    {
        "ticker": "WIPRO",
        "status": "CLOSED",
        "direction": "BUY",
        "entry_price": 168.0,
        "quantity": 50,
        "target1": 178.0,
        "target2": 186.0,
        "initial_sl": 162.0,
        "setup_date": today,
        "entry_date": today,
        "exit_date": today,
        "exit_price": 176.0,
        "pnl": 400.0,
        "ltp": 176.0,
        "trade_type": "POSITIONAL"
    },
]

# ─── MOCK WATCHLIST ───────────────────────────────────────────────────────────
MOCK_WATCHLIST = [
    "AXISBANK|NR7|BUY",
    "ICICIBANK|NR7|SELL",
    "LTIM|NR7|BUY",
    "TATAMOTORS|Swing|Long",
    "MARUTI|Swing|Long",
    "SUNPHARMA|Swing|Short",
    "DRREDDY|Swing|Short",
    "NESTLEIND|Swing|Long",
]

# ─── MOCK PNL LOG (closed trades) ────────────────────────────────────────────
MOCK_PNL_LOG_HEADER = "ticker,direction,entry_price,exit_price,quantity,pnl,entry_date,exit_date,trade_type\n"
MOCK_PNL_LOG_ROWS = [
    "WIPRO,BUY,168.0,176.0,50,400.0,{t},{t},POSITIONAL\n".format(t=today),
    "SBIN,SELL,1060.0,1044.0,10,160.0,{t},{t},INTRADAY\n".format(t=today),
    "HCLTECH,BUY,1220.0,1195.0,5,-125.0,{t},{t},POSITIONAL\n".format(t=today),
]


def inject_mock():
    os.makedirs(DATA_DIR, exist_ok=True)

    # Write paper trades
    with open(PAPER_TRADES_FILE, "w") as f:
        json.dump(MOCK_TRADES, f, indent=2)
    print(f"[MOCK] Wrote {len(MOCK_TRADES)} trades -> {PAPER_TRADES_FILE}")

    # Write watchlist
    with open(WATCHLIST_FILE, "w") as f:
        f.write("\n".join(MOCK_WATCHLIST) + "\n")
    print(f"[MOCK] Wrote {len(MOCK_WATCHLIST)} watchlist items -> {WATCHLIST_FILE}")

    # Write PnL log
    with open(PNL_LOG_FILE, "w") as f:
        f.write(MOCK_PNL_LOG_HEADER)
        f.writelines(MOCK_PNL_LOG_ROWS)
    print(f"[MOCK] Wrote {len(MOCK_PNL_LOG_ROWS)} PnL log entries -> {PNL_LOG_FILE}")

    print("\n[OK] Mock data injected. Open http://127.0.0.1:8080 to verify the dashboard.")


def reset_data():
    # Empty trades
    with open(PAPER_TRADES_FILE, "w") as f:
        json.dump([], f)
    # Empty watchlist
    with open(WATCHLIST_FILE, "w") as f:
        f.write("")
    # Empty PnL log
    with open(PNL_LOG_FILE, "w") as f:
        f.write(MOCK_PNL_LOG_HEADER)
    print("✅ Data reset to empty state.")


if __name__ == "__main__":
    if "--reset" in sys.argv:
        reset_data()
    else:
        inject_mock()
