import os
import sys
import json
import warnings
warnings.filterwarnings("ignore")
warnings.simplefilter(action='ignore', category=DeprecationWarning)
warnings.simplefilter(action='ignore', category=FutureWarning)

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import csv

# Add src/ to sys.path so we can import connectors correctly
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.join(BASE_DIR, "src"))

import db_storage

from connectors import angelone
from scanner import core

app = FastAPI(title="Trader Pilot Breakout Stocks Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import threading

@app.on_event("startup")
def start_scanner_background_thread():
    if BASE_DIR not in sys.path:
        sys.path.append(BASE_DIR)
    try:
        import breakout_scanner
        thread = threading.Thread(target=breakout_scanner.run_scanner_loop, daemon=True)
        thread.start()
        print("[INFO] Scanner engine background thread started successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to start scanner engine thread: {e}")

DATA_DIR = os.path.join(BASE_DIR, "data")
PAPER_TRADES_FILE = os.path.join(DATA_DIR, "paper_trades.json")

def get_fallback_price(ticker):
    """Single-ticker fallback via yfinance (last close)."""
    try:
        df = yf.download(ticker + ".NS", period="5d", progress=False, auto_adjust=True)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return float(df.iloc[-1]["Close"])
    except Exception as e:
        print(f"Fallback yfinance price fetch error for {ticker}: {e}")
    return 0.0


def bulk_yfinance_prices(tickers: list) -> dict:
    """Bulk-fetch last close prices for all tickers via yfinance.
    Works on weekends, market holidays and after-hours.
    Returns dict: {TICKER: price}
    """
    if not tickers:
        return {}
    ns_tickers = [t + ".NS" for t in tickers]
    prices = {}
    try:
        df = yf.download(ns_tickers, period="5d", progress=False, auto_adjust=True)
        if not df.empty:
            close = df["Close"] if "Close" in df.columns else df.xs("Close", axis=1, level=0)
            # If single ticker, df["Close"] is a Series
            if isinstance(close, pd.Series):
                last = float(close.dropna().iloc[-1]) if not close.dropna().empty else 0.0
                prices[tickers[0]] = last
            else:
                latest = close.ffill().iloc[-1]
                for ns, orig in zip(ns_tickers, tickers):
                    v = latest.get(ns, None)
                    prices[orig] = float(v) if v is not None and not pd.isna(v) else 0.0
    except Exception as e:
        print(f"[yfinance] Bulk price fetch error: {e}")
        # Final per-ticker fallback
        for t in tickers:
            prices[t] = get_fallback_price(t)
    return prices

@app.get("/")
def get_dashboard():
    """Serves the dashboard index.html page."""
    index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": f"index.html not found at: {index_path}"}

@app.get("/brand_icon.jpg")
@app.get("/favicon.ico")
def get_brand_icon():
    """Serves the official VYUHA brand icon and favicon."""
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand_icon.jpg")
    if os.path.exists(icon_path):
        return FileResponse(icon_path, media_type="image/jpeg")
    fallback = os.path.join(BASE_DIR, "assets", "brand_icon.jpg")
    if os.path.exists(fallback):
        return FileResponse(fallback, media_type="image/jpeg")
    return {"error": "Brand icon not found"}

@app.get("/api/trades")
def get_trades():
    """Loads paper trades and updates them with live LTP, projections, Nifty indices, and watchlist."""
    trades = db_storage.get_paper_trades()
    
    # Read watchlist
    watchlist = db_storage.get_watchlist()

    # Fetch Nifty index data
    nifty_ltp = 0.0
    nifty_change_pct = 0.0
    if angelone.is_authenticated:
        try:
            nifty_res = angelone.smart.getMarketData("OHLC", {"NSE": ["26000"]})
            if nifty_res.get("status") and "data" in nifty_res:
                fetched = nifty_res["data"].get("fetched", [])
                if fetched:
                    item = fetched[0]
                    nifty_ltp = float(item["ltp"])
                    prev_close = float(item["close"])
                    if prev_close > 0:
                        nifty_change_pct = ((nifty_ltp - prev_close) / prev_close) * 100
        except Exception as e:
            print(f"Error fetching Nifty live quote: {e}")
            
    nifty_projection = "PROJ_NONE_0.0"
    try:
        # Download 1-month data to allow core.predict_future_trend to run
        nifty_df = yf.download("^NSEI", period="1mo", progress=False, auto_adjust=True)
        if not nifty_df.empty:
            if isinstance(nifty_df.columns, pd.MultiIndex):
                nifty_df.columns = nifty_df.columns.get_level_values(0)
            
            nifty_projection = core.predict_future_trend(nifty_df)
            
            if nifty_ltp == 0.0:
                nifty_ltp = float(nifty_df.iloc[-1]["Close"])
                prev_close = float(nifty_df.iloc[-2]["Close"]) if len(nifty_df) > 1 else nifty_ltp
                if prev_close > 0:
                    nifty_change_pct = ((nifty_ltp - prev_close) / prev_close) * 100
    except Exception as e:
        print(f"Nifty fallback fetch error: {e}")

    # Filter active and pending tickers
    active_or_pending = [t for t in trades if t["status"] in ["PENDING", "ACTIVE", "PARTIAL_EXIT"]]
    trade_tickers = [t["ticker"] for t in active_or_pending]
    
    # Extract watchlist tickers
    watchlist_tickers = []
    for wl_item in watchlist:
        parts = wl_item.split('|')
        if len(parts) > 0:
            watchlist_tickers.append(parts[0].strip())
            
    tickers = list(set(trade_tickers + watchlist_tickers))
    
    # Fetch live quotes — try Angel One first, bulk yfinance fallback otherwise
    live_quotes = {}
    ao_success = False
    if angelone.is_authenticated and tickers:
        try:
            live_quotes = angelone.get_live_ohlc(tickers)
            ao_success = bool(live_quotes)
        except Exception as e:
            print(f"[AngelOne] get_live_ohlc error: {e}")

    if not ao_success and tickers:
        print(f"[INFO] Angel One offline/unauthenticated — using yfinance bulk fallback for {tickers}")
        yf_prices = bulk_yfinance_prices(tickers)
        for ticker, price in yf_prices.items():
            live_quotes[ticker] = {"last_price": price, "high": price, "low": price}

    # Compile live trades list
    updated_trades = []
    total_realized_pnl = 0.0
    total_unrealized_pnl = 0.0
    
    # Load closed trades PnL from log
    try:
        pnl_logs = db_storage.get_pnl_log()
        for row in pnl_logs:
            try:
                total_realized_pnl += float(row.get("pnl", 0.0))
            except Exception:
                pass
    except Exception as e:
        print(f"Error loading PnL log: {e}")

    for trade in trades:
        ticker = trade["ticker"]
        status = trade["status"]
        direction = trade["direction"]
        entry = float(trade["entry_price"])
        qty = int(trade["quantity"])
        t1 = float(trade["target1"])
        t2 = float(trade["target2"])
        sl = float(trade["initial_sl"])
        
        ltp = 0.0
        high = 0.0
        low = 0.0
        
        # Calculate Projected Profit & Loss
        risk_per_share = abs(entry - sl)
        projected_loss = risk_per_share * qty
        projected_profit = abs(t1 - entry) * (qty / 2) + abs(t2 - entry) * (qty / 2)
        
        trade["projected_loss"] = projected_loss
        trade["projected_profit"] = projected_profit
        
        # Calculate Duration
        from datetime import datetime
        date_to_use = trade.get("entry_date") or trade.get("setup_date")
        trade_type_val = trade.get("trade_type", "POSITIONAL").upper()
        duration = "0 Days"
        if date_to_use:
            try:
                dt = datetime.strptime(date_to_use, "%Y-%m-%d")
                days = (datetime.now() - dt).days
                if trade_type_val == "INTRADAY":
                    duration = "Today" if days == 0 else f"{days} Days"
                else:
                    duration = f"Day {days + 1}"
            except Exception:
                pass
        trade["duration"] = duration
        
        # Attach live price
        if status in ["PENDING", "ACTIVE", "PARTIAL_EXIT"]:
            # Use live_quotes if available, else yfinance single-ticker fallback
            if ticker in live_quotes:
                quote = live_quotes[ticker]
                ltp = quote["last_price"]
                high = quote.get("high", ltp)
                low = quote.get("low", ltp)
            else:
                ltp = get_fallback_price(ticker)
                high = ltp
                low = ltp
                
            trade["ltp"] = ltp
            trade["high"] = high
            trade["low"] = low
            
            # Calculate Unrealized PnL
            unrealized_pnl = 0.0
            if status == "ACTIVE":
                if direction == "BUY":
                    unrealized_pnl = (ltp - entry) * qty
                else:
                    unrealized_pnl = (entry - ltp) * qty
            elif status == "PARTIAL_EXIT":
                if direction == "BUY":
                    realized_half = (t1 - entry) * (qty / 2)
                    unrealized_half = (ltp - entry) * (qty / 2)
                else:
                    realized_half = (entry - t1) * (qty / 2)
                    unrealized_half = (entry - ltp) * (qty / 2)
                unrealized_pnl = realized_half + unrealized_half
                
            trade["pnl"] = unrealized_pnl
            total_unrealized_pnl += unrealized_pnl
        else:
            trade["ltp"] = trade.get("exit_price", entry)
            trade["pnl"] = trade.get("pnl", 0.0)
            
        updated_trades.append(trade)
        
    # Attach LTP to watchlist items
    watchlist_objects = []
    for wl_item in watchlist:
        parts = wl_item.split('|')
        if len(parts) > 0:
            ticker = parts[0].strip()
            strategy = parts[1].strip() if len(parts) > 1 else ""
            direction = parts[2].strip() if len(parts) > 2 else ""
            trade_type = parts[3].strip() if len(parts) > 3 else "POSITIONAL"
            entry_price_str = parts[4].strip() if len(parts) > 4 else ""
            proj_str = parts[5].strip() if len(parts) > 5 else "PROJ_NONE_0.0"
            
            entry_price_val = None
            if entry_price_str:
                try:
                    entry_price_val = float(entry_price_str)
                except ValueError:
                    pass
            
            wl_ltp = 0.0
            if ticker in live_quotes:
                wl_ltp = live_quotes[ticker]["last_price"]
            # No extra call — bulk_yfinance_prices already covered watchlist tickers above
                
            watchlist_objects.append({
                "ticker": ticker,
                "strategy": strategy,
                "direction": direction,
                "trade_type": trade_type,
                "ltp": wl_ltp,
                "entry_price": entry_price_val,
                "projection": proj_str
            })
        
    return {
        "trades": updated_trades,
        "watchlist": watchlist_objects,
        "nifty": {
            "ltp": nifty_ltp,
            "change_pct": nifty_change_pct,
            "trend_up": nifty_change_pct >= 0,
            "regime": core.get_market_regime(),
            "projection": nifty_projection
        },
        "summary": {
            "realized_pnl": total_realized_pnl,
            "unrealized_pnl": total_unrealized_pnl,
            "total_pnl": total_realized_pnl + total_unrealized_pnl,
            "active_count": len([t for t in trades if t["status"] in ["ACTIVE", "PARTIAL_EXIT"]]),
            "pending_count": len([t for t in trades if t["status"] == "PENDING"]),
            "online": angelone.is_authenticated
        }
    }

@app.get("/api/trade-history")
def get_trade_history(from_date: str = "", to_date: str = ""):
    """Returns closed trade history from pnl_log.csv, filtered by optional date range."""
    rows = []
    try:
        pnl_logs = db_storage.get_pnl_log()
        for row in pnl_logs:
            exit_date = row.get("date", "")
            if from_date and exit_date < from_date:
                continue
            if to_date and exit_date > to_date:
                continue
            try:
                rows.append({
                    "ticker": row.get("ticker", ""),
                    "direction": row.get("direction", ""),
                    "entry_price": float(row.get("entry", 0)),
                    "exit_price": float(row.get("exit_price", 0)),
                    "quantity": 1, # Default since quantity isn't logged
                    "pnl": float(row.get("pnl", 0)),
                    "entry_date": row.get("date", ""),
                    "exit_date": exit_date,
                    "trade_type": row.get("trade_type", ""),
                    "reason": row.get("reason", ""),
                    "strategy": row.get("strategy", "NR7")
                })
            except Exception:
                pass
    except Exception as e:
        print(f"Error loading trade history: {e}")
    total_pnl = sum(r["pnl"] for r in rows)
    winners = len([r for r in rows if r["pnl"] > 0])
    losers = len([r for r in rows if r["pnl"] < 0])
    
    # Calculate performance by strategy
    strategy_perf = {}
    for r in rows:
        st = r["strategy"]
        if st not in strategy_perf:
            strategy_perf[st] = {"trades": 0, "winners": 0, "pnl": 0.0}
        strategy_perf[st]["trades"] += 1
        strategy_perf[st]["pnl"] += r["pnl"]
        if r["pnl"] > 0:
            strategy_perf[st]["winners"] += 1
            
    # Find best strategy
    best_strategy = None
    best_pnl = -float('inf')
    for st, data in strategy_perf.items():
        if data["trades"] >= 1 and data["pnl"] > best_pnl:
            best_pnl = data["pnl"]
            best_strategy = st

    return {
        "trades": rows, 
        "total_pnl": total_pnl, 
        "winners": winners, 
        "losers": losers,
        "strategy_performance": strategy_perf,
        "best_strategy": best_strategy
    }

@app.post("/api/trades/toggle_real/{ticker}")
def toggle_real_trade(ticker: str):
    """Toggles the is_real_trade flag for the active/pending trade of the given ticker."""
    try:
        trades = db_storage.get_paper_trades()
        found = False
        new_status = False
        for t in trades:
            if t["ticker"] == ticker and t["status"] in ["PENDING", "ACTIVE", "PARTIAL_EXIT"]:
                current = t.get("is_real_trade", False)
                t["is_real_trade"] = not current
                new_status = t["is_real_trade"]
                found = True
                break
                
        if found:
            db_storage.save_paper_trades(trades)
            return {"status": "success", "is_real_trade": new_status}
        else:
            return {"status": "error", "message": "Active trade not found for ticker"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

import socket
import argparse

def get_local_ip():
    """Detects local LAN IP for seamless laptop/tablet access on the same Wi-Fi."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def run_server():
    parser = argparse.ArgumentParser(description="Trader Pilot Web Dashboard & Scanner Engine")
    parser.add_argument("--host", default=os.environ.get("DASHBOARD_HOST", "0.0.0.0"), help="Host address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("DASHBOARD_PORT", 8080)), help="Port number (default: 8080)")
    parser.add_argument("--no-browser", action="store_true", help="Do not attempt to auto-launch local web browser")
    args, _ = parser.parse_known_args()

    local_ip = get_local_ip()

    print("\n" + "=" * 70)
    print("🌐  TRADER PILOT WEB DASHBOARD & SCANNER ENGINE")
    print("=" * 70)
    print(f"📱 Local Phone URL:    http://localhost:{args.port}  (or http://127.0.0.1:{args.port})")
    if local_ip != "127.0.0.1":
        print(f"💻 Laptop / Wi-Fi URL: http://{local_ip}:{args.port}")
    else:
        print(f"💻 Laptop / Wi-Fi URL: http://<YOUR_PHONE_WIFI_IP>:{args.port}")
    print("   (Ensure your phone and laptop are connected to the same Wi-Fi/Hotspot)")
    print("=" * 70 + "\n")

    if not args.no_browser:
        import webbrowser
        from threading import Timer
        def open_browser():
            try:
                webbrowser.open(f"http://127.0.0.1:{args.port}/")
            except Exception:
                pass
        Timer(1.5, open_browser).start()

    uvicorn.run(app, host=args.host, port=args.port, access_log=False, log_level="warning")

if __name__ == "__main__":
    try:
        run_server()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"UVICORN CRASH: {e}")
