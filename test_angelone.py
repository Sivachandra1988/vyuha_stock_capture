import angelone_connector
import time

def run_test():
    print("Testing Angel One Connector Initialization...")
    
    # Check if already authenticated on import, otherwise initialize
    authenticated = angelone_connector.is_authenticated
    if not authenticated:
        authenticated = angelone_connector.initialize_session()
    
    if authenticated:
        print("\n[SUCCESS] Authentication Successful!")
        print("Testing live market feed for LLOYDSENGG and TEXINFRA...")
        
        # Test fetching OHLC quotes
        quotes = angelone_connector.get_live_ohlc(["LLOYDSENGG", "TEXINFRA"])
        if quotes:
            print("\n--- Live Market Data ---")
            for ticker, data in quotes.items():
                print(f"\nTicker: {ticker}")
                print(f"  Open:  Rs. {data['open']:.2f}")
                print(f"  High:  Rs. {data['high']:.2f}")
                print(f"  Low:   Rs. {data['low']:.2f}")
                print(f"  LTP:   Rs. {data['last_price']:.2f}")
                print(f"  Prev Close: Rs. {data['prev_close']:.2f}")
            print("------------------------")
            print("\nAll Angel One Connector features are working successfully!")
        else:
            print("\nFailed to retrieve market data. Check if your API Key supports Market Data (Publisher/Market Data API).")
    else:
        print("\nFailed to authenticate. Please configure your credentials in 'angelone_config.json'.")

if __name__ == "__main__":
    run_test()
