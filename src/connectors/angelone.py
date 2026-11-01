import json
import os
import requests
import pyotp
from SmartApi import SmartConnect

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(CONFIG_DIR, "angelone_config.json")
SESSION_FILE = os.path.join(DATA_DIR, "angelone_session.json")
TOKENS_MAP_FILE = os.path.join(DATA_DIR, "angelone_tokens.json")
SCRIP_MASTER_FILE = os.path.join(DATA_DIR, "OpenAPIScripMaster.json")
SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

default_config = {
    "api_key": "YOUR_API_KEY",
    "client_code": "YOUR_CLIENT_CODE",
    "password": "YOUR_PASSWORD",
    "totp_key": "YOUR_TOTP_SECRET_KEY"
}

# Create default config if missing
if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "w") as f:
        json.dump(default_config, f, indent=4)
    print(f"Created {CONFIG_FILE}. Please configure your Angel One credentials.")

# Load config
try:
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
except Exception as e:
    config = default_config
    print(f"Error loading config: {e}")

api_key = config.get("api_key", "YOUR_API_KEY")
client_code = config.get("client_code", "YOUR_CLIENT_CODE")
password = config.get("password", "YOUR_PASSWORD")
totp_key = config.get("totp_key", "YOUR_TOTP_SECRET_KEY")

smart = SmartConnect(api_key=api_key)
scrip_master_cache = {}
is_authenticated = False

def load_scrip_master():
    """Loads scrip master into memory from lightweight file, building it if missing."""
    global scrip_master_cache
    if scrip_master_cache:
        return
        
    # Check if optimized tokens file exists
    if os.path.exists(TOKENS_MAP_FILE) and os.path.getsize(TOKENS_MAP_FILE) > 0:
        try:
            with open(TOKENS_MAP_FILE, "r") as f:
                scrip_master_cache = json.load(f)
            return
        except Exception as e:
            print(f"Error reading {TOKENS_MAP_FILE}: {e}. Rebuilding...")

    # Otherwise, download and generate it
    print(f"{TOKENS_MAP_FILE} not found. Building token lookup database...")
    if not os.path.exists(SCRIP_MASTER_FILE) or os.path.getsize(SCRIP_MASTER_FILE) == 0:
        print("Downloading Angel One Scrip Master (approx. 15MB)...")
        try:
            r = requests.get(SCRIP_MASTER_URL, timeout=30)
            if r.status_code == 200:
                with open(SCRIP_MASTER_FILE, "wb") as f:
                    f.write(r.content)
            else:
                print(f"Failed to download Scrip Master: {r.status_code}")
                return
        except Exception as e:
            print(f"Error downloading Scrip Master: {e}")
            return
            
    print("Parsing Scrip Master and generating lightweight tokens map...")
    try:
        temp_cache = {}
        with open(SCRIP_MASTER_FILE, "r") as f:
            scrips = json.load(f)
            for s in scrips:
                if s.get("exch_seg") == "NSE" and s.get("symbol", "").endswith("-EQ"):
                    symbol = s.get("symbol", "")
                    ticker = symbol.replace("-EQ", "")
                    temp_cache[ticker] = s.get("token")
        
        # Save lightweight file
        with open(TOKENS_MAP_FILE, "w") as f:
            json.dump(temp_cache, f, indent=4)
            
        scrip_master_cache = temp_cache
        print(f"Optimized token map saved. Loaded {len(scrip_master_cache)} NSE Equity scrips.")
        
        # Clean up the large 15MB master file
        if os.path.exists(SCRIP_MASTER_FILE):
            os.remove(SCRIP_MASTER_FILE)
            
    except Exception as e:
        print(f"Error building tokens map: {e}")

def get_token(ticker):
    """Gets the unique symbol token for a given ticker."""
    load_scrip_master()
    return scrip_master_cache.get(ticker)

def initialize_session():
    """Attempts to log in to Angel One using stored session or credentials + TOTP."""
    global smart, is_authenticated
    if is_authenticated:
        return True
        
    # Load session
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                sess_data = json.load(f)
                jwt_token = sess_data.get("jwtToken")
                refresh_token = sess_data.get("refreshToken")
                feed_token = sess_data.get("feedToken")
                if jwt_token and refresh_token and feed_token:
                    # Reinitialize client with tokens
                    smart = SmartConnect(api_key=api_key, jwt_token=jwt_token, refresh_token=refresh_token, feed_token=feed_token)
                    # Verify session with a profile check
                    smart.getProfile(refresh_token)
                    is_authenticated = True
                    return True
        except Exception:
            print("Cached session invalid. Re-authenticating...")

    # Log in using credentials + TOTP
    if any(x == default_config[k] for k, x in [("api_key", api_key), ("client_code", client_code), ("password", password), ("totp_key", totp_key)]):
        print("Angel One credentials not fully configured in config file.")
        is_authenticated = False
        return False
        
    print("Logging in to Angel One via SmartAPI...")
    try:
        totp = pyotp.TOTP(totp_key).now()
        data = smart.generateSession(client_code, password, totp)
        if data.get("status"):
            tokens = data["data"]
            # Save session
            with open(SESSION_FILE, "w") as f:
                json.dump({
                    "jwtToken": tokens["jwtToken"],
                    "refreshToken": tokens["refreshToken"],
                    "feedToken": tokens["feedToken"]
                }, f)
            print("Session authenticated and saved successfully.")
            is_authenticated = True
            return True
        else:
            print(f"Login failed: {data.get('message')}")
            is_authenticated = False
            return False
    except Exception as e:
        print(f"Authentication error: {e}")
        is_authenticated = False
        return False

# Attempt automatic initialization
if api_key != "YOUR_API_KEY" and client_code != "YOUR_CLIENT_CODE":
    try:
        is_authenticated = initialize_session()
    except Exception as e:
        print(f"Automatic session init failed: {e}")

def get_live_ohlc(tickers):
    """
    Fetches live OHLC data for the given tickers from Angel One.
    Returns: dict of {ticker: {"open": float, "high": float, "low": float, "close": float, "last_price": float}}
    """
    if not is_authenticated:
        return {}
        
    # Map tickers to tokens
    token_map = {}
    tokens = []
    for ticker in tickers:
        token = get_token(ticker)
        if token:
            token_map[token] = ticker
            tokens.append(token)
            
    if not tokens:
        print(f"No tokens found for tickers: {tickers}")
        return {}
        
    # Chunk tokens in batches of 50 to avoid 'Tokens max limit exceeded'
    chunk_size = 50
    token_chunks = [tokens[i:i + chunk_size] for i in range(0, len(tokens), chunk_size)]
    
    result = {}
    for chunk in token_chunks:
        try:
            response = smart.getMarketData("OHLC", {"NSE": chunk})
            if response.get("status") and "data" in response:
                fetched = response["data"].get("fetched", [])
                for item in fetched:
                    token = item.get("symbolToken")
                    ticker = token_map.get(token)
                    if ticker:
                        result[ticker] = {
                            "open": float(item["open"]),
                            "high": float(item["high"]),
                            "low": float(item["low"]),
                            "close": float(item["ltp"]),
                            "last_price": float(item["ltp"]),
                            "prev_close": float(item["close"])
                        }
            else:
                print(f"Market data fetch chunk failed: {response.get('message')}")
        except Exception as e:
            print(f"Error fetching live data chunk: {e}")
    return result
