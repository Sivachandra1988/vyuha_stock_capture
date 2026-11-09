import os
import sys
import json
import numpy as np
import pandas as pd
import yfinance as yf

# Define all recommendations from today's alert stream (Nov 09, 2026)
TODAY_TRADES = [
    # Swing signals
    {
        "ticker": "LAURUSLABS",
        "strategy": "Swing",
        "direction": "BUY",
        "entry": 2437.90,
        "sl": 2309.50,
        "target1": 2669.02,
        "target2": 2758.90,
        "reason": "Confluence Breakout"
    },
    {
        "ticker": "SUNPHARMA",
        "strategy": "Swing",
        "direction": "BUY",
        "entry": 1930.20,
        "sl": 1823.75,
        "target1": 2121.81,
        "target2": 2196.32,
        "reason": "Confluence Breakout"
    },
    {
        "ticker": "SBIN",
        "strategy": "Swing",
        "direction": "BUY",
        "entry": 1040.20,
        "sl": 995.75,
        "target1": 1120.20,
        "target2": 1151.32,
        "reason": "Confluence Breakout"
    },
    {
        "ticker": "HCLTECH",
        "strategy": "Swing",
        "direction": "SELL",
        "entry": 1415.00,
        "sl": 1448.08,
        "target1": 1355.46,
        "target2": 1332.31,
        "reason": "Confluence Breakout"
    },
    # Vyuha-BO Institutional signals
    {
        "ticker": "BHARTIARTL",
        "strategy": "Vyuha-BO",
        "direction": "SELL",
        "entry": 2048.10,
        "sl": 2094.76,
        "target1": 1964.11,
        "target2": 1949.95,
        "reason": "EQUILIBRIUM_VAL_BREAKDOWN"
    },
    {
        "ticker": "GRASIM",
        "strategy": "Vyuha-BO",
        "direction": "SELL",
        "entry": 3179.60,
        "sl": 3219.10,
        "target1": 3108.50,
        "target2": 3080.85,
        "reason": "EQUILIBRIUM_VAL_BREAKDOWN"
    },
    {
        "ticker": "ONGC",
        "strategy": "Vyuha-BO",
        "direction": "BUY",
        "entry": 234.40,
        "sl": 229.27,
        "target1": 243.64,
        "target2": 247.23,
        "reason": "EQUILIBRIUM_VAH_BREAKOUT"
    },
    {
        "ticker": "WIPRO",
        "strategy": "Vyuha-BO",
        "direction": "SELL",
        "entry": 195.99,
        "sl": 200.13,
        "target1": 188.55,
        "target2": 187.32,
        "reason": "EQUILIBRIUM_VAL_BREAKDOWN"
    },
    {
        "ticker": "INFY",
        "strategy": "Vyuha-BO",
        "direction": "SELL",
        "entry": 1149.20,
        "sl": 1177.38,
        "target1": 1098.48,
        "target2": 1078.75,
        "reason": "EQUILIBRIUM_VAL_BREAKDOWN"
    },
    {
        "ticker": "TRENT",
        "strategy": "Vyuha-BO",
        "direction": "SELL",
        "entry": 3032.50,
        "sl": 3069.95,
        "target1": 2965.09,
        "target2": 2938.87,
        "reason": "EQUILIBRIUM_VAL_BREAKDOWN (Whipsaw Short)"
    },
    {
        "ticker": "TRENT",
        "strategy": "Vyuha-BO",
        "direction": "BUY",
        "entry": 3073.90,
        "sl": 3045.00,
        "target1": 3125.00,
        "target2": 3150.00,
        "reason": "EQUILIBRIUM_VAH_BREAKOUT (Revenge Long)"
    },
    # Realtime breakout triggers
    {
        "ticker": "AXISBANK",
        "strategy": "Realtime",
        "direction": "BUY",
        "entry": 1305.60,
        "sl": 1285.00,
        "target1": 1342.00,
        "target2": 1360.00,
        "reason": "Breakout Trigger"
    },
    {
        "ticker": "HINDALCO",
        "strategy": "Realtime",
        "direction": "BUY",
        "entry": 950.20,
        "sl": 932.00,
        "target1": 982.00,
        "target2": 1000.00,
        "reason": "Breakout Trigger"
    },
    {
        "ticker": "CUMMINSIND",
        "strategy": "Realtime",
        "direction": "BUY",
        "entry": 4109.70,
        "sl": 4040.00,
        "target1": 4235.00,
        "target2": 4300.00,
        "reason": "Breakout Trigger"
    },
    {
        "ticker": "ASIANPAINT",
        "strategy": "Realtime",
        "direction": "SELL",
        "entry": 2498.00,
        "sl": 2525.00,
        "target1": 2450.00,
        "target2": 2420.00,
        "reason": "Breakdown Trigger"
    },
    {
        "ticker": "POWERGRID",
        "strategy": "Realtime",
        "direction": "SELL",
        "entry": 257.90,
        "sl": 261.50,
        "target1": 251.40,
        "target2": 248.00,
        "reason": "Breakdown Trigger"
    },
    {
        "ticker": "MAXHEALTH",
        "strategy": "Vyuha-BO",
        "direction": "SELL",
        "entry": 914.95,
        "sl": 931.10,
        "target1": 885.88,
        "target2": 870.00,
        "reason": "Supply Rejection & Equilibrium Breakdown"
    }
]

def clean_df(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

def run_today_backtest():
    print("=" * 80)
    print("AUDIT & BACKTEST REPORT: ALL RECOMMENDATIONS ON 2026-11-09")
    print("=" * 80)
    
    results = []
    
    # Download data for all tickers for today (5-day 15m)
    for item in TODAY_TRADES:
        ticker = item["ticker"]
        direction = item["direction"]
        entry = item["entry"]
        sl = item["sl"]
        t1 = item["target1"]
        t2 = item["target2"]
        strat = item["strategy"]
        
        try:
            df = yf.download(ticker + ".NS", period="5d", interval="15m", progress=False, auto_adjust=True)
            df = clean_df(df)
            if df.empty:
                print(f"[WARN] No intraday data for {ticker}")
                continue
                
            # Filter for today (2026-11-09)
            # Find all bars for the latest day
            dates = df.index.strftime('%Y-%m-%d')
            today_date = dates[-1]
            df_today = df[dates == today_date]
            
            if df_today.empty:
                print(f"[WARN] No bars for today for {ticker}")
                continue
                
            # Find the first bar where entry is reachable or closest
            # In our live stream, entry happened during the morning or midday
            day_open = float(df_today['Open'].iloc[0])
            day_high = float(df_today['High'].max())
            day_low = float(df_today['Low'].min())
            day_close = float(df_today['Close'].iloc[-1])
            
            # Simulate bar by bar from entry
            trade_active = False
            outcome = "OPEN"
            exit_price = day_close
            exit_time = df_today.index[-1].strftime("%H:%M")
            hit_t1 = False
            
            for idx, bar in df_today.iterrows():
                b_high = float(bar['High'])
                b_low = float(bar['Low'])
                b_close = float(bar['Close'])
                b_time = idx.strftime("%H:%M")
                
                # Check trigger condition if not active yet
                if not trade_active:
                    if direction == "BUY" and b_high >= entry:
                        trade_active = True
                    elif direction == "SELL" and b_low <= entry:
                        trade_active = True
                    if not trade_active:
                        continue
                        
                # If active, check SL and Targets
                if direction == "BUY":
                    if b_low <= sl:
                        outcome = "SL HIT"
                        exit_price = sl
                        exit_time = b_time
                        break
                    if b_high >= t1 and not hit_t1:
                        hit_t1 = True
                    if b_high >= t2:
                        outcome = "T2 HIT"
                        exit_price = t2
                        exit_time = b_time
                        break
                elif direction == "SELL":
                    if b_high >= sl:
                        outcome = "SL HIT"
                        exit_price = sl
                        exit_time = b_time
                        break
                    if b_low <= t1 and not hit_t1:
                        hit_t1 = True
                    if b_low <= t2:
                        outcome = "T2 HIT"
                        exit_price = t2
                        exit_time = b_time
                        break
            
            if trade_active and outcome == "OPEN":
                if hit_t1:
                    outcome = "T1 HIT (HOLDING)"
                    exit_price = day_close
                else:
                    outcome = "IN PROGRESS (EOD)"
                    exit_price = day_close
            elif not trade_active:
                outcome = "UNTRIGGERED"
                exit_price = entry

            # Calculate Spot PnL (₹1,000 risk unit)
            risk_per_share = abs(entry - sl)
            qty = max(1, int(1000 / risk_per_share)) if risk_per_share > 0 else 100
            
            if direction == "BUY":
                spot_pnl = (exit_price - entry) * qty
                pct_return = ((exit_price - entry) / entry) * 100.0
            else:
                spot_pnl = (entry - exit_price) * qty
                pct_return = ((entry - exit_price) / entry) * 100.0
                
            results.append({
                "ticker": ticker,
                "strategy": strat,
                "direction": direction,
                "entry": entry,
                "exit": exit_price,
                "sl": sl,
                "t1": t1,
                "outcome": outcome,
                "exit_time": exit_time,
                "pnl_rs": round(spot_pnl, 2),
                "return_pct": round(pct_return, 2),
                "day_range": f"{day_low:.2f} - {day_high:.2f}",
                "day_close": round(day_close, 2)
            })
        except Exception as e:
            print(f"Error testing {ticker}: {e}")
            
    res_df = pd.DataFrame(results)
    print("\n--- RESULTS TABLE ---")
    print(res_df.to_string(index=False))
    
    total_pnl = res_df['pnl_rs'].sum()
    wins = len(res_df[res_df['pnl_rs'] > 0])
    losses = len(res_df[res_df['pnl_rs'] < 0])
    scratches = len(res_df[res_df['pnl_rs'] == 0])
    win_rate = (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0
    
    print("\n" + "=" * 80)
    print(f"TOTAL TRADES EVALUATED: {len(res_df)}")
    print(f"WINS: {wins} | LOSSES: {losses} | FLAT: {scratches}")
    print(f"WIN RATE: {win_rate:.1f}%")
    print(f"NET SPOT P&L (₹1,000 Risk Model): ₹{total_pnl:,.2f}")
    print("=" * 80)

if __name__ == "__main__":
    run_today_backtest()
