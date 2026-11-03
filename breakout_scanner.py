import os
import sys
import json
import time
import warnings
warnings.filterwarnings("ignore")
warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=UserWarning)

import requests
import socket
import threading
from datetime import datetime, time as dt_time

# Force UTF-8 output on Windows to prevent charmap codec errors with emoji characters
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

# Set Indian Standard Time (IST) on POSIX/Linux platforms
if hasattr(time, "tzset"):
    os.environ.setdefault("TZ", "Asia/Kolkata")
    time.tzset()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from connectors import angelone
from scanner import core, eod, trades, swing, advanced_trend, vyuha
import db_storage

# Telegram credentials (reads from environment variables with fallback)
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "7714086057:AAHOadi3WMgKxl3Pbd3BO5zTANRPCEo5YJs")
# Vyuha Daily / Swing stocks group (-5132723378)
VYUHA_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "-5132723378")

DISCLAIMER_TAG = "NOT SEBI registered"
STANDARD_FOOTER = (
    "\n\n_⚠️ Not SEBI registered. Strictly educational. Mandatory generic SL (1.5x ATR / max 2% risk)._"
)

# Files pathing
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.txt")
OUTDATED_FILE = os.path.join(DATA_DIR, "outdated_watchlist.txt")
ALERTS_STATE_FILE = os.path.join(DATA_DIR, "alerted_today.json")

ALIGN_WITH_MARKET_TREND = True
CHECK_TIMINGS = ["09:30", "11:00", "13:00", "14:30", "15:15"]
SWING_TIMINGS = ["10:15", "11:15", "12:15", "13:15", "14:15", "15:15"]

# 15 minute interval timings from 9:30 AM to 3:15 PM
ADVANCED_TREND_TIMINGS = [
    "09:30", "09:45", "10:00", "10:15", "10:30", "10:45", 
    "11:00", "11:15", "11:30", "11:45", "12:00", "12:15", 
    "12:30", "12:45", "13:00", "13:15", "13:30", "13:45", 
    "14:00", "14:15", "14:30", "14:45", "15:00", "15:15"
]

alerted_today = set()
eod_scanned_today = ""
current_date = datetime.now().strftime("%Y-%m-%d")

def send_telegram(message, chat_id=None):
    """Sends a markdown message with mandatory SEBI disclaimer & generic SL rule to the Telegram group."""
    target_chat = chat_id if chat_id else VYUHA_CHAT_ID

    if DISCLAIMER_TAG not in message:
        message = message.rstrip() + STANDARD_FOOTER

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            print(f"[WARN] Telegram error ({target_chat}): {r.status_code} {r.text}")
    except Exception as e:
        print(f"Telegram error: {e}")

def load_alerts_state():
    global alerted_today, current_date
    today_str = datetime.now().strftime("%Y-%m-%d")
    current_date = today_str
    if os.path.exists(ALERTS_STATE_FILE):
        try:
            with open(ALERTS_STATE_FILE, "r") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    alerted_today = set(data.get("alerts", []))
                    print(f"Loaded {len(alerted_today)} active alerts for today ({today_str}).")
                    return
        except Exception as e:
            print(f"Error loading alerts state: {e}")
    alerted_today = set()
    save_alerts_state()

def save_alerts_state():
    try:
        with open(ALERTS_STATE_FILE, "w") as f:
            json.dump({
                "date": current_date,
                "alerts": list(alerted_today)
            }, f, indent=4)
    except Exception as e:
        print(f"Error saving alerts state: {e}")

def get_nifty_trend():
    """Gets broader market regime."""
    return core.get_market_regime()

def execute_scan_cycle():
    """Executes a single scheduled scan cycle over the watchlist stocks."""
    global alerted_today
        
    original_lines = db_storage.get_watchlist()
        
    tickers_map = {}
    for line in original_lines:
        parts = line.split('|')
        tickers_map[parts[0].strip()] = line
        
    tickers = list(tickers_map.keys())
        
    if not tickers:
        print("Watchlist is empty. No stocks to scan.")
        return
        
    print(f"\n--- [INFO] Scan cycle started @ {datetime.now().strftime('%H:%M:%S')} ---")
    print(f"Scanning watchlist: {tickers}")
    
    market_trend = "NEUTRAL"
    if ALIGN_WITH_MARKET_TREND:
        market_trend = get_nifty_trend()
        print(f"Nifty 50 market trend: {market_trend}")
        
        if market_trend == "CHOPPY":
            print("[INFO] Market is currently CHOPPY. Running scan cycle anyway, but setups may be volatile.")

    keep_list = []
    remove_list = []
    
    # Load active paper trades to exclude already taken trades from watchlist
    pt = trades.load_paper_trades()
    active_tickers = set([t["ticker"] for t in pt if t["status"] in ["ACTIVE", "PARTIAL_EXIT"]])

    # Fetch live quotes to validate prices across all watchlist strategies
    live_quotes = {}
    if angelone.is_authenticated and tickers:
        try:
            live_quotes = angelone.get_live_ohlc(tickers)
        except Exception:
            pass
    if not live_quotes and tickers:
        try:
            from dashboard.server import bulk_yfinance_prices
            yf_prices = bulk_yfinance_prices(tickers)
            for t, p in yf_prices.items():
                if p > 0:
                    live_quotes[t] = {"last_price": p}
        except Exception:
            pass

    triggered_candidates = []
    for ticker in tickers:
        if ticker in active_tickers:
            remove_list.append(ticker)
            print(f"Removing {ticker} from watchlist. Reason: Trade is already active.")
            continue
            
        line = tickers_map.get(ticker, ticker)
        parts = [p.strip() for p in line.split('|')]
        strategy = parts[1].lower() if len(parts) > 1 else ""
        
        # Check swing / breakout / smc / ai recommendations for SL breach or target expiration
        if strategy == "swing" or "breakout" in strategy or "smc" in strategy or "ai" in strategy:
            entry_price = None
            if len(parts) > 4 and parts[4]:
                try:
                    entry_price = float(parts[4])
                except ValueError:
                    pass
            
            direction_str = parts[2].upper() if len(parts) > 2 else "BUY"
            
            if entry_price and entry_price > 0 and ticker in live_quotes:
                curr_ltp = live_quotes[ticker].get("last_price", 0.0)
                if curr_ltp > 0:
                    pct_move = ((curr_ltp - entry_price) / entry_price) * 100.0
                    
                    # For Longs: prune if price collapsed > 4.5% below entry (SL breach) or exceeded 15% (expired)
                    if direction_str in ["BUY", "LONG", "B/O CALL"]:
                        if pct_move < -4.5 or pct_move > 15.0:
                            remove_list.append(ticker)
                            print(f"Purging outdated {strategy} recommendation: {ticker} (Move: {pct_move:+.1f}%)")
                            continue
                    # For Shorts: prune if price rose > 2.5% above entry (SL breach) or dropped > 15% (expired)
                    elif direction_str in ["SELL", "SHORT"]:
                        if pct_move > 2.5 or pct_move < -15.0:
                            remove_list.append(ticker)
                            print(f"Purging outdated {strategy} recommendation: {ticker} (Move: {pct_move:+.1f}%)")
                            continue

            keep_list.append(ticker)
            continue
            
        print(f"Scanning {ticker}...")
        action, result = core.scan_stock(ticker, market_trend, strict=False, max_days=3)
        
        if action == "KEEP":
            # If a trigger condition is met, initiate paper trade, notify, and remove from watchlist
            if result.get("trigger"):
                alert_key = f"{ticker}_{result.get('status')}_{result.get('setup_date')}"
                if alert_key not in alerted_today:
                    triggered_candidates.append((ticker, result, strategy))
                    alerted_today.add(alert_key)
                    result["strategy"] = strategy
                    trades.initiate_paper_trade(ticker, result)
                # Remove from watchlist since the trade has now been triggered and is active
                remove_list.append(ticker)
                print(f"Removing {ticker} from watchlist. Reason: Trade triggered.")
            else:
                keep_list.append(ticker)
        else:
            # The stock no longer has a valid NR7 setup in the last 3 days, remove it.
            remove_list.append(ticker)
            print(f"Removing {ticker} from watchlist. Reason: {result.get('status')}")

    if triggered_candidates:
        save_alerts_state()
        lines_out = [f"⚡ *VYUHA BREAKOUT TRIGGERS* ({len(triggered_candidates)} Active)\n"]
        for ticker, res, strat in triggered_candidates:
            dir_label = "🟢 BUY" if res.get("direction", "BUY").upper() in ["BUY", "LONG"] else "🔴 SELL"
            lines_out.append(
                f"• *{dir_label}: #{ticker}*\n"
                f"  Entry: `{res.get('entry', 0.0):.2f}` | SL: `{res.get('sl', 0.0):.2f}` | T1: `{res.get('target1', 0.0):.2f}`\n"
                f"  Reason: NR7 Breakout ({strat.upper()}) | Hold: Positional / 1–3 Days"
            )
        send_telegram("\n\n".join(lines_out))

    # Database auto-purges any trigger exits via remove_from_watchlist above, 
    # but for outdated structures we also purge:
    for ticker in remove_list:
        db_storage.remove_from_watchlist(ticker)
            
    # Log removed stocks
    if remove_list:
        with open(OUTDATED_FILE, "a") as f:
            for ticker in remove_list:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {ticker}\n")

    # Update active paper positions
    trades.update_and_manage_trades(send_telegram)
    print(f"[Done] Kept: {len(keep_list)} | Removed: {len(remove_list)}")

def is_market_open():
    """Checks if the market is open today using Angel One API and market hours."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False, "Weekend"
        
    current_time = now.time()
    market_start = dt_time(9, 15)
    market_end = dt_time(15, 30)
    
    if not (market_start <= current_time <= market_end):
        return False, f"Outside Market Hours (09:15 - 15:30), current: {now.strftime('%H:%M:%S')}"
        
    # Check for holiday via LTP data
    if angelone.is_authenticated:
        try:
            res = angelone.smart.ltpData("NSE", "Nifty 50", "26000")
            if res and res.get("status") and "data" in res:
                data = res["data"]
                ltp_price = float(data.get("ltp", 0.0))
                if ltp_price == 0.0:
                    return False, "Market Holiday today (LTP is 0.0)"
        except Exception as e:
            print(f"[WARN] Error validating holiday via Angel API: {e}")
            
    return True, "Market Open"

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def is_port_in_use(port, host="127.0.0.1"):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False

def ensure_dashboard_server_running(port=8080, host="0.0.0.0"):
    """Auto-starts the FastAPI web dashboard in background if not already active."""
    if os.environ.get("NO_WEB_UI", "0") == "1":
        return

    if is_port_in_use(port, "127.0.0.1"):
        print(f"🌐 [INFO] Web Dashboard is active on port {port}.")
        return

    try:
        import uvicorn
        from dashboard.server import app
        config = uvicorn.Config(app=app, host=host, port=port, access_log=False, log_level="warning")
        server = uvicorn.Server(config)
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        
        local_ip = get_local_ip()
        print("=" * 70)
        print("🌐  VYUHA TRADER-PILOT — INTEGRATED DASHBOARD & SCANNER")
        print("=" * 70)
        print(f"📱 Mobile Web UI:      http://localhost:{port}  (or http://127.0.0.1:{port})")
        if local_ip != "127.0.0.1":
            print(f"💻 Laptop / Wi-Fi URL: http://{local_ip}:{port}")
        else:
            print(f"💻 Laptop / Wi-Fi URL: http://<PHONE_IP>:{port}")
        print("   (Both Web UI and Breakout Scanner are running in this same session)")
        print("=" * 70 + "\n")
    except Exception as e:
        print(f"[WARN] Could not auto-start dashboard background server: {e}")

def run_scanner_loop(continuous=True, daemon=False, force=False):
    try:
        print("===============================================================")
        mode_str = "24/7 DAEMON" if daemon else ("CONTINUOUS (TILL 3:30 PM MARKET CLOSE)" if continuous else "SINGLE RUN")
        print(f" [INFO] MODULAR TRADER PILOT ENGINE ({mode_str})")
        print(f" Checking timings (NR7): {', '.join(CHECK_TIMINGS)}")
        print(f" Checking timings (Swing): {', '.join(SWING_TIMINGS)}")
        print(f" Checking timings (Adv Trend): Every 15m (09:30 - 15:15)")
        print(" Sending Telegram alerts to configured chat.")
        print("===============================================================\n")
        
        load_alerts_state()
        
        open_status, reason = is_market_open()
        if not open_status and not force:
            print(f"⏸️ [INFO] Market is CLOSED ({reason}).")
            if not daemon:
                print("💡 Tip: To run continuous execution anyway, pass: python3 breakout_scanner.py --force")
                print("💡 Tip: To run 24/7 background polling, pass: python3 breakout_scanner.py --daemon\n")
                return
            else:
                print("[INFO] Daemon mode active. Waiting for market open...")

        print(f"[INFO] Engine active: {'FORCED EXECUTION' if force and not open_status else reason}")
        
        # Auto-launch Web Dashboard in background if not already running
        port = int(os.environ.get("DASHBOARD_PORT", 8080))
        ensure_dashboard_server_running(port=port)
        
        # Auto-populate if watchlist is completely empty
        if not os.path.exists(WATCHLIST_FILE) or os.path.getsize(WATCHLIST_FILE) == 0:
            print("[INFO] Watchlist is empty on startup. Forcing an initial swing scan...")
            market_trend = get_nifty_trend()
            swing.execute_swing_scan(send_telegram, force_full=True, market_trend=market_trend)
            
        execute_scan_cycle()

        # If user explicitly requested a single run, exit now
        if not continuous and not daemon:
            print("[INFO] Single scan cycle complete. Process released.\n")
            return

        print(f"\n🟢 [INFO] Continuous live monitoring started @ {datetime.now().strftime('%H:%M:%S')} IST.")
        print("   Monitoring active trades every 30s | Running 15m & hourly scans till 3:30 PM...\n")
            
        global eod_scanned_today
        while True:
            open_status, reason = is_market_open()
            if not open_status and not force:
                now_t = datetime.now().time()
                # If market just closed past 15:30 IST and not daemon, complete session
                if now_t >= dt_time(15, 30) and not daemon:
                    print(f"\n🏁 [INFO] Market closed at {datetime.now().strftime('%H:%M:%S')} IST. Daily trading session completed.")
                    print("   All intraday trades squared off and swing positions logged.")
                    break
                    
                print(f"[INFO] Market is CLOSED ({reason}). Engine sleeping (5m)...")
                time.sleep(300)
                continue
                
            today_str = datetime.now().strftime("%Y-%m-%d")
            now_str = datetime.now().strftime("%H:%M")
            
            # Continuously monitor active trades for targets/stop loss
            try:
                trades.update_and_manage_trades(send_telegram)
            except Exception as e:
                print(f"[ERROR] Trade management error: {e}")
            
            # Check and trigger EOD scan at 3:15 PM - 3:30 PM
            if "15:15" <= now_str <= "15:30" and eod_scanned_today != today_str:
                market_trend = get_nifty_trend()
                eod.run_eod_scan(send_telegram, market_trend=market_trend)
                print(f"[INFO] Running scheduled VYUHA EOD Confluence Scan...")
                vyuha.run_vyuha_daily_scan(send_telegram)
                eod_scanned_today = today_str
                
            # Trigger swing scans hourly
            if now_str in SWING_TIMINGS:
                market_trend = get_nifty_trend()
                force_full = (now_str == "10:15")
                print(f"[INFO] Running scheduled Swing scan cycle at {now_str} (Force Full Universe Rebuild: {force_full})...")
                swing.execute_swing_scan(send_telegram, force_full=force_full, market_trend=market_trend)
                time.sleep(61)
                continue
                
            # Trigger Advanced Trend & VYUHA Intraday Scan every 15 minutes
            if now_str in ADVANCED_TREND_TIMINGS:
                market_trend = get_nifty_trend()
                print(f"[INFO] Running scheduled Advanced Trend scan cycle at {now_str}...")
                advanced_trend.execute_advanced_trend_scan(send_telegram, market_trend=market_trend)
                print(f"[INFO] Running scheduled VYUHA Intraday Confluence scan cycle at {now_str}...")
                vyuha.execute_vyuha_intraday_scan(send_telegram, market_trend=market_trend)
                time.sleep(61)
                continue
                
            if now_str in CHECK_TIMINGS:
                print(f"[INFO] Running scheduled NR7 scan cycle at {now_str}...")
                execute_scan_cycle()
                time.sleep(61)
            else:
                time.sleep(30)
    except KeyboardInterrupt:
        print("\n👋 VCP Scanner stopped gracefully. Goodbye!")
    except Exception as e:
        print(f"[ERROR] Scanner engine encountered an error: {e}")

if __name__ == "__main__":
    # Command line args to manually run EOD or Swing scan tests
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg == "--test-eod":
            eod.run_eod_scan(send_telegram, market_trend="NEUTRAL")
            sys.exit(0)
        elif arg == "--test-swing":
            swing.execute_swing_scan(send_telegram, force_full=True, market_trend="NEUTRAL")
            sys.exit(0)
        elif arg == "--test-advanced":
            advanced_trend.execute_advanced_trend_scan(send_telegram, market_trend="NEUTRAL")
            sys.exit(0)
        elif arg == "--test-vyuha-daily":
            vyuha.run_vyuha_daily_scan(send_telegram)
            sys.exit(0)
        elif arg == "--test-vyuha-intraday":
            vyuha.execute_vyuha_intraday_scan(send_telegram, market_trend="NEUTRAL")
            sys.exit(0)
        elif arg in ["--once", "--single", "-s"]:
            run_scanner_loop(continuous=False, daemon=False)
            sys.exit(0)
        elif arg in ["--daemon", "-d"]:
            run_scanner_loop(continuous=True, daemon=True)
            sys.exit(0)
        elif arg in ["--force", "-f"]:
            run_scanner_loop(continuous=True, daemon=False, force=True)
            sys.exit(0)

    # Default: Run continuously during live market hours till 3:30 PM close
    run_scanner_loop(continuous=True, daemon=False)
