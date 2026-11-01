import zerodha_connector
import time

def run_test():
    print("Testing Zerodha Connector Initialization...")
    
    # Check if config is loaded
    print(f"Loaded API Key: {zerodha_connector.api_key}")
    
    # 1. Initialize session
    authenticated = zerodha_connector.initialize_session()
    
    if not authenticated:
        print("\nACTION REQUIRED:")
        print("Please click the link above, log in to Zerodha, and authorize.")
        print("After authorization, you will be redirected to a URL like:")
        print("  http://127.0.0.1/?request_token=XXXXXX&action=login...")
        print("\nPlease copy the 'request_token' value (the XXXXXX part) and enter it below.")
        
        request_token = input("Enter request_token: ").strip()
        if not request_token:
            print("No request token entered. Test aborted.")
            return
            
        success = zerodha_connector.generate_session(request_token)
        if not success:
            print("Failed to generate session. Please check your request token or API credentials.")
            return
        
        # Reload state
        zerodha_connector.is_authenticated = zerodha_connector.initialize_session()
        
    if zerodha_connector.is_authenticated:
        print("\n[SUCCESS] Authentication Successful!")
        print("Testing live market feed for LLOYDSENGG and TEXINFRA...")
        
        # Test fetching OHLC quotes
        quotes = zerodha_connector.get_live_ohlc(["LLOYDSENGG", "TEXINFRA"])
        print("\n--- Live Market Data ---")
        for ticker, data in quotes.items():
            print(f"\nTicker: {ticker}")
            print(f"  Open:  ₹{data['open']:.2f}")
            print(f"  High:  ₹{data['high']:.2f}")
            print(f"  Low:   ₹{data['low']:.2f}")
            print(f"  LTP:   ₹{data['last_price']:.2f}")
            print(f"  Prev Close: ₹{data['prev_close']:.2f}")
        print("------------------------")
        print("\nAll Zerodha Connector features are working successfully!")
    else:
        print("\nFailed to authenticate. Please check your credentials.")

if __name__ == "__main__":
    run_test()
