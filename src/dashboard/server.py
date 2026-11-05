import os
import sys

# Force UTF-8 encoding
if hasattr(sys.stdout, "reconfigure") and sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure") and sys.stderr.encoding != "utf-8":
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import json
import warnings
warnings.filterwarnings("ignore")
warnings.simplefilter(action='ignore', category=DeprecationWarning)
warnings.simplefilter(action='ignore', category=FutureWarning)

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import csv
import time

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
    
    # Clean temporary disk data and SQLite caches on application startup
    try:
        import cleanup
        cleanup.cleanup_all_temp_data(verbose=False)
    except Exception:
        pass

    try:
        import breakout_scanner
        thread = threading.Thread(
            target=breakout_scanner.run_scanner_loop,
            kwargs={"continuous": True, "daemon": True},
            daemon=True
        )
        thread.start()
        print("[INFO] Scanner engine background thread started successfully in 24/7 daemon mode.")
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

# Quote cache: {ticker: {"last_price": float, "high": float, "low": float, "timestamp": float}}
_QUOTE_CACHE = {}
_QUOTE_CACHE_TTL = 8.0  # seconds

# Nifty LTP & 24h change cache
_NIFTY_CACHE = {
    "timestamp": 0.0,
    "ltp": 0.0,
    "change_pct": 0.0,
    "trend_up": True
}
_NIFTY_CACHE_TTL = 20.0  # seconds

# Nifty 1-month ML Trend Projection cache (expensive - avoid frequent yfinance downloads)
_NIFTY_PROJECTION_CACHE = {
    "timestamp": 0.0,
    "projection": "PROJ_NONE_0.0"
}
_NIFTY_PROJ_CACHE_TTL = 120.0  # seconds (2 minutes)

# Market Regime cache (6mo Nifty ADX/SMA alignment - expensive)
_MARKET_REGIME_CACHE = {
    "timestamp": 0.0,
    "regime": "BULLISH"
}
_MARKET_REGIME_CACHE_TTL = 300.0  # seconds (5 minutes)


def get_cached_market_regime() -> str:
    """Caches 6-month regime evaluation to avoid yfinance rate limits."""
    global _MARKET_REGIME_CACHE
    now = time.time()
    if (now - _MARKET_REGIME_CACHE["timestamp"] > _MARKET_REGIME_CACHE_TTL) or not _MARKET_REGIME_CACHE["regime"]:
        try:
            regime = core.get_market_regime()
            if regime:
                _MARKET_REGIME_CACHE = {"timestamp": now, "regime": regime}
        except Exception as e:
            print(f"[get_cached_market_regime] Error: {e}")
    return _MARKET_REGIME_CACHE["regime"]


def get_cached_nifty() -> dict:
    """Returns cached Nifty index data, refreshing LTP every 20s and ML projection every 2m."""
    global _NIFTY_CACHE, _NIFTY_PROJECTION_CACHE
    now = time.time()

    # Refresh 1mo ML trend projection if TTL expired
    if now - _NIFTY_PROJECTION_CACHE["timestamp"] > _NIFTY_PROJ_CACHE_TTL:
        try:
            nifty_df = yf.download("^NSEI", period="1mo", progress=False, auto_adjust=True)
            if not nifty_df.empty:
                if isinstance(nifty_df.columns, pd.MultiIndex):
                    nifty_df.columns = nifty_df.columns.get_level_values(0)
                proj = core.predict_future_trend(nifty_df)
                _NIFTY_PROJECTION_CACHE = {"timestamp": now, "projection": proj}
                # Also if Nifty LTP cache is still 0, seed it from 1mo data
                if _NIFTY_CACHE["ltp"] == 0.0:
                    last_c = float(nifty_df.iloc[-1]["Close"])
                    prev_c = float(nifty_df.iloc[-2]["Close"]) if len(nifty_df) > 1 else last_c
                    chg = ((last_c - prev_c) / prev_c) * 100 if prev_c > 0 else 0.0
                    _NIFTY_CACHE = {
                        "timestamp": now,
                        "ltp": last_c,
                        "change_pct": chg,
                        "trend_up": chg >= 0
                    }
        except Exception as e:
            print(f"[get_cached_nifty] Projection fetch error: {e}")

    # Refresh LTP & change_pct if TTL expired
    if now - _NIFTY_CACHE["timestamp"] > _NIFTY_CACHE_TTL or _NIFTY_CACHE["ltp"] == 0.0:
        fetched_live = False
        if angelone.is_authenticated:
            try:
                nifty_res = angelone.smart.getMarketData("OHLC", {"NSE": ["26000"]})
                if nifty_res.get("status") and "data" in nifty_res:
                    fetched = nifty_res["data"].get("fetched", [])
                    if fetched:
                        item = fetched[0]
                        n_ltp = float(item["ltp"])
                        prev_c = float(item["close"])
                        n_chg = ((n_ltp - prev_c) / prev_c) * 100 if prev_c > 0 else 0.0
                        _NIFTY_CACHE = {
                            "timestamp": now,
                            "ltp": n_ltp,
                            "change_pct": n_chg,
                            "trend_up": n_chg >= 0
                        }
                        fetched_live = True
            except Exception as e:
                print(f"[get_cached_nifty] Angel One quote error: {e}")

        if not fetched_live and (_NIFTY_CACHE["ltp"] == 0.0 or now - _NIFTY_CACHE["timestamp"] > _NIFTY_CACHE_TTL):
            try:
                nifty_fast = yf.download("^NSEI", period="5d", progress=False, auto_adjust=True)
                if not nifty_fast.empty:
                    if isinstance(nifty_fast.columns, pd.MultiIndex):
                        nifty_fast.columns = nifty_fast.columns.get_level_values(0)
                    last_c = float(nifty_fast.iloc[-1]["Close"])
                    prev_c = float(nifty_fast.iloc[-2]["Close"]) if len(nifty_fast) > 1 else last_c
                    chg = ((last_c - prev_c) / prev_c) * 100 if prev_c > 0 else 0.0
                    _NIFTY_CACHE = {
                        "timestamp": now,
                        "ltp": last_c,
                        "change_pct": chg,
                        "trend_up": chg >= 0
                    }
            except Exception as e:
                print(f"[get_cached_nifty] yfinance fallback error: {e}")

    return {
        "ltp": _NIFTY_CACHE["ltp"],
        "change_pct": _NIFTY_CACHE["change_pct"],
        "trend_up": _NIFTY_CACHE["trend_up"],
        "projection": _NIFTY_PROJECTION_CACHE["projection"]
    }


def fetch_quotes_with_cache(tickers: list) -> dict:
    """Fetches live quotes for tickers, checking _QUOTE_CACHE first.
    Only queries exchange / yfinance for items older than _QUOTE_CACHE_TTL.
    Returns dict: {ticker: {"last_price": float, "high": float, "low": float}}
    """
    global _QUOTE_CACHE
    now = time.time()
    result = {}
    needed = []

    for t in tickers:
        if not t:
            continue
        cached = _QUOTE_CACHE.get(t)
        if cached and (now - cached.get("timestamp", 0) < _QUOTE_CACHE_TTL) and cached.get("last_price", 0.0) > 0:
            result[t] = cached
        else:
            needed.append(t)

    if not needed:
        return result

    fetched = {}
    ao_success = False
    if angelone.is_authenticated:
        try:
            ao_quotes = angelone.get_live_ohlc(needed)
            if ao_quotes:
                ao_success = True
                for t, q in ao_quotes.items():
                    lp = float(q.get("last_price", 0.0))
                    if lp > 0:
                        fetched[t] = {
                            "last_price": lp,
                            "high": float(q.get("high", lp)),
                            "low": float(q.get("low", lp)),
                            "timestamp": now
                        }
        except Exception as e:
            print(f"[AngelOne] get_live_ohlc error: {e}")

    if not ao_success and needed:
        yf_prices = bulk_yfinance_prices(needed)
        for t, price in yf_prices.items():
            if price > 0:
                fetched[t] = {
                    "last_price": price,
                    "high": price,
                    "low": price,
                    "timestamp": now
                }

    for t, q in fetched.items():
        _QUOTE_CACHE[t] = q
        result[t] = q

    # Final per-ticker fallback if still missing
    for t in needed:
        if t not in result:
            if t in _QUOTE_CACHE and _QUOTE_CACHE[t].get("last_price", 0.0) > 0:
                result[t] = _QUOTE_CACHE[t]
            else:
                fb = get_fallback_price(t)
                q_val = {"last_price": fb, "high": fb, "low": fb, "timestamp": now}
                if fb > 0:
                    _QUOTE_CACHE[t] = q_val
                result[t] = q_val

    return result


@app.get("/api/trades")
def get_trades(view: str = Query("auto")):
    """Loads paper trades and updates them with live LTP, projections, Nifty indices, and watchlist.
    Only queries live quotes for the tickers currently active on the user's screen (view) to minimize
    API load, avoid rate-limiting, and conserve mobile battery/data.
    """
    trades = db_storage.get_paper_trades()
    watchlist = db_storage.get_watchlist()

    # Nifty data and market regime from cache
    nifty_info = get_cached_nifty()
    nifty_ltp = nifty_info["ltp"]
    nifty_change_pct = nifty_info["change_pct"]
    nifty_projection = nifty_info["projection"]
    trend_up = nifty_info["trend_up"]
    regime = get_cached_market_regime()

    # Extract trade tickers
    active_or_pending = [t for t in trades if t.get("status") in ["PENDING", "ACTIVE", "PARTIAL_EXIT"]]
    trade_tickers = list(set([t["ticker"] for t in active_or_pending if t.get("ticker")]))

    # Extract watchlist tickers
    watchlist_tickers = []
    for wl_item in watchlist:
        parts = wl_item.split('|')
        if len(parts) > 0 and parts[0].strip():
            watchlist_tickers.append(parts[0].strip())
    watchlist_tickers = list(set(watchlist_tickers))

    # Determine tickers to query based on view
    view_lower = (view or "auto").lower()
    if view_lower in ["positions", "active"]:
        tickers_to_query = [t["ticker"] for t in trades if t.get("status") in ["ACTIVE", "PARTIAL_EXIT"] and t.get("ticker")]
    elif view_lower == "pending":
        tickers_to_query = [t["ticker"] for t in trades if t.get("status") == "PENDING" and t.get("ticker")]
    elif view_lower == "watchlist":
        tickers_to_query = watchlist_tickers
    else:
        # "all" or "auto"
        tickers_to_query = list(set(trade_tickers + watchlist_tickers))

    # Fetch live quotes for only the active view (cached if under TTL)
    live_quotes = fetch_quotes_with_cache(tickers_to_query)

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
        ticker = trade.get("ticker", "")
        status = trade.get("status", "")
        direction = trade.get("direction", "BUY")
        entry = float(trade.get("entry_price", 0.0))
        qty = int(trade.get("quantity", 1))
        t1 = float(trade.get("target1", 0.0))
        t2 = float(trade.get("target2", 0.0))
        sl = float(trade.get("initial_sl", 0.0))

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
            # Check live_quotes first, then cached quote in _QUOTE_CACHE, else entry
            quote = live_quotes.get(ticker) or _QUOTE_CACHE.get(ticker)
            if quote:
                ltp = quote["last_price"]
                high = quote.get("high", ltp)
                low = quote.get("low", ltp)
            else:
                ltp = entry
                high = entry
                low = entry

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

    # Attach LTP and New/Today categorization to watchlist items
    watchlist_objects = []
    today_recs = db_storage.get_today_recommendations()
    today_tickers = set(item["ticker"] for item in today_recs)
    today_map = {item["ticker"]: item for item in today_recs}
    today_str = datetime.now().strftime("%Y-%m-%d")

    seen_tickers = set()

    for wl_item in watchlist:
        parts = wl_item.split('|')
        if len(parts) > 0:
            ticker = parts[0].strip()
            if not ticker or ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)

            strategy = parts[1].strip() if len(parts) > 1 else ""
            direction = parts[2].strip() if len(parts) > 2 else ""
            trade_type = parts[3].strip() if len(parts) > 3 else "POSITIONAL"
            entry_price_str = parts[4].strip() if len(parts) > 4 else ""
            proj_str = parts[5].strip() if len(parts) > 5 else "PROJ_NONE_0.0"
            date_str = parts[9].strip() if len(parts) > 9 else ""

            entry_price_val = None
            if entry_price_str:
                try:
                    entry_price_val = float(entry_price_str)
                except ValueError:
                    pass

            quote = live_quotes.get(ticker) or _QUOTE_CACHE.get(ticker)
            wl_ltp = quote["last_price"] if quote else (entry_price_val or 0.0)

            rec_info = today_map.get(ticker, {})
            is_new = (ticker in today_tickers) or (date_str == today_str)

            watchlist_objects.append({
                "ticker": ticker,
                "strategy": strategy or rec_info.get("strategy", ""),
                "direction": direction or rec_info.get("direction", ""),
                "trade_type": trade_type or rec_info.get("trade_type", "POSITIONAL"),
                "ltp": wl_ltp,
                "entry_price": entry_price_val if entry_price_val is not None else rec_info.get("entry_price"),
                "projection": proj_str,
                "is_new": is_new,
                "added_date": today_str if is_new else (date_str or "Earlier")
            })

    # Ensure any fresh daily recommendations are surfaced in the watchlist
    for rec in today_recs:
        t_rec = rec.get("ticker")
        if t_rec and t_rec not in seen_tickers:
            seen_tickers.add(t_rec)
            quote = live_quotes.get(t_rec) or _QUOTE_CACHE.get(t_rec)
            wl_ltp = quote["last_price"] if quote else rec.get("entry_price", 0.0)
            watchlist_objects.insert(0, {
                "ticker": t_rec,
                "strategy": rec.get("strategy", "Swing"),
                "direction": rec.get("direction", "BUY"),
                "trade_type": rec.get("trade_type", "POSITIONAL"),
                "ltp": wl_ltp,
                "entry_price": rec.get("entry_price", 0.0),
                "projection": "PROJ_NONE_0.0",
                "is_new": True,
                "added_date": today_str
            })

    return {
        "trades": updated_trades,
        "watchlist": watchlist_objects,
        "nifty": {
            "ltp": nifty_ltp,
            "change_pct": nifty_change_pct,
            "trend_up": trend_up,
            "regime": regime,
            "projection": nifty_projection
        },
        "summary": {
            "realized_pnl": total_realized_pnl,
            "unrealized_pnl": total_unrealized_pnl,
            "total_pnl": total_realized_pnl + total_unrealized_pnl,
            "active_count": len([t for t in trades if t.get("status") in ["ACTIVE", "PARTIAL_EXIT"]]),
            "pending_count": len([t for t in trades if t.get("status") == "PENDING"]),
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

from pydantic import BaseModel
from typing import Optional

class WatchlistAddRequest(BaseModel):
    ticker: str
    strategy: Optional[str] = "Swing"
    direction: Optional[str] = "Long"
    trade_type: Optional[str] = "POSITIONAL"
    entry_price: Optional[float] = None

@app.post("/api/watchlist/add")
def add_to_watchlist(req: WatchlistAddRequest):
    ticker = req.ticker.strip().upper().replace(".NS", "").replace(".BO", "")
    if not ticker:
        return {"status": "error", "message": "Ticker symbol cannot be empty"}
    
    strategy = (req.strategy or "Swing").strip()
    direction = (req.direction or "Long").strip()
    trade_type = (req.trade_type or "POSITIONAL").strip().upper()
    
    entry_str = f"{ticker} | {strategy} | {direction} | {trade_type}"
    if req.entry_price is not None and req.entry_price > 0:
        entry_str += f" | {req.entry_price}"
    
    try:
        db_storage.append_to_watchlist(entry_str)
        db_storage.record_daily_recommendation(
            ticker=ticker,
            strategy=strategy,
            direction=direction,
            trade_type=trade_type,
            entry=req.entry_price or 0.0,
            reason="Manual Addition"
        )
        return {"status": "success", "ticker": ticker, "entry": entry_str}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.delete("/api/watchlist/{ticker}")
@app.post("/api/watchlist/delete/{ticker}")
def delete_from_watchlist(ticker: str):
    clean_ticker = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
    try:
        db_storage.remove_from_watchlist(clean_ticker)
        return {"status": "success", "deleted": clean_ticker}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/chart/{ticker}")
def get_chart_data(ticker: str, period: str = "3mo"):
    clean_ticker = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
    try:
        df = yf.download(f"{clean_ticker}.NS", period=period, interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            df = yf.download(f"{clean_ticker}.BO", period=period, interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            return {"status": "error", "message": f"No chart data found for {clean_ticker}"}
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        candles = []
        for idx, row in df.iterrows():
            candles.append({
                "time": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row.get("Volume", 0))
            })
        return {"status": "success", "ticker": clean_ticker, "candles": candles}
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
