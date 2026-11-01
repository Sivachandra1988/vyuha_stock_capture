import json
import os
from kiteconnect import KiteConnect

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(CONFIG_DIR, "zerodha_config.json")
SESSION_FILE = os.path.join(DATA_DIR, "zerodha_session.json")

default_config = {
    "api_key": "YOUR_API_KEY",
    "api_secret": "YOUR_API_SECRET"
}

# Create default config if missing
if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "w") as f:
        json.dump(default_config, f, indent=4)
    print(f"Created {CONFIG_FILE}. Please configure your API credentials.")

# Load config
try:
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
except Exception as e:
    config = default_config
    print(f"Error loading {CONFIG_FILE}: {e}")

api_key = config.get("api_key", "YOUR_API_KEY")
api_secret = config.get("api_secret", "YOUR_API_SECRET")

# Initialize Kite
kite = KiteConnect(api_key=api_key)
is_authenticated = False

def get_login_url():
    """Generates the login URL to authorize the app."""
    return kite.login_url()

def generate_session(request_token):
    """Exchanges request_token for access_token and saves the session."""
    global is_authenticated
    try:
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]
        kite.set_access_token(access_token)
        
        # Save session to file
        with open(SESSION_FILE, "w") as f:
            json.dump({"access_token": access_token}, f)
        print("Session authorized and saved successfully.")
        is_authenticated = True
        return True
    except Exception as e:
        print(f"Error generating session: {e}")
        is_authenticated = False
        return False

def initialize_session():
    """Loads a saved session if valid, otherwise instructs user to log in."""
    global is_authenticated
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                session = json.load(f)
                access_token = session.get("access_token")
                if access_token:
                    kite.set_access_token(access_token)
                    # Verify session with a profile call
                    kite.profile()
                    is_authenticated = True
                    return True
        except Exception:
            print("Saved session invalid or expired. Re-authentication required.")
            
    print("\n================ ZERODHA AUTHENTICATION REQUIRED ================")
    print("Please open the following URL in your browser and authorize:")
    print(get_login_url())
    print("=================================================================\n")
    is_authenticated = False
    return False

# Attempt automatic initialization on import
if api_key != "YOUR_API_KEY" and api_secret != "YOUR_API_SECRET":
    try:
        is_authenticated = initialize_session()
    except Exception as e:
        print(f"Automatic session init failed: {e}")

def get_live_ohlc(tickers):
    """
    Fetches OHLC data for the given list of tickers from Zerodha.
    Returns: dict of {ticker: {"open": float, "high": float, "low": float, "close": float, "last_price": float}}
    """
    if not is_authenticated:
        print("Zerodha connector not authenticated. Cannot fetch live data.")
        return {}
        
    instruments = [f"NSE:{t}" for t in tickers]
    try:
        data = kite.ohlc(instruments)
        result = {}
        for ticker in tickers:
            inst_key = f"NSE:{ticker}"
            if inst_key in data:
                item = data[inst_key]
                result[ticker] = {
                    "open": float(item["ohlc"]["open"]),
                    "high": float(item["ohlc"]["high"]),
                    "low": float(item["ohlc"]["low"]),
                    "close": float(item["last_price"]),
                    "last_price": float(item["last_price"]),
                    "prev_close": float(item["ohlc"]["close"])
                }
        return result
    except Exception as e:
        print(f"Error fetching live data from Zerodha: {e}")
        return {}
