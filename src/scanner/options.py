"""
=============================================================================
🎯 INSTITUTIONAL NSE OPTIONS RECOMMENDATION ENGINE
=============================================================================
Provides automated, high-precision Option Contract recommendations for
Vyuha-BO, Swing, and Intraday stock setups:
1. Dynamic Monthly Expiry Resolution (Last Thursday of active trading cycle)
2. Precise Strike Step & ATM/ITM Strike Calculation
3. Comprehensive NSE F&O Lot Size Lookup
4. Greeks Delta (~0.50 ATM) Projection for Option SL & Targets
5. Dual-Role Strategy:
   - Primary: BUY ATM CE (Long) / BUY ATM PE (Short)
   - Alternative: SELL OTM PE @ Demand Floor / SELL OTM CE @ Supply Ceiling
=============================================================================
"""

import os
import sys
import json
import datetime
from datetime import timedelta

# Comprehensive NSE F&O Lot Sizes (Updated for active symbols)
NSE_FO_LOT_SIZES = {
    # High Volume Large Caps
    "RELIANCE": 250, "TCS": 175, "HDFCBANK": 550, "ICICIBANK": 700, "INFY": 400,
    "SBIN": 750, "BHARTIARTL": 475, "ITC": 1600, "LICI": 700, "HINDUNILVR": 300,
    "LT": 175, "BAJFINANCE": 125, "HCLTECH": 350, "MARUTI": 50, "SUNPHARMA": 350,
    "ADANIENT": 300, "TATAMOTORS": 550, "KOTAKBANK": 400, "NTPC": 1500, "TITAN": 175,
    "AXISBANK": 625, "ONGC": 3850, "POWERGRID": 1900, "COALINDIA": 2100, "TATASTEEL": 5500,
    "BAJAJFINSV": 500, "M&M": 350, "ADANIPORTS": 400, "ASIANPAINT": 200, "JSWSTEEL": 675,
    
    # Midcap & Swing High-Beta F&O
    "SHRIRAMFIN": 750, "INDIGO": 125, "EICHERMOT": 150, "HINDALCO": 1050, "BEL": 2850,
    "TMPV": 2000, "JIOFIN": 2450, "DRREDDY": 125, "LAURUSLABS": 850, "ADANIPOWER": 1250,
    "VBL": 625, "CUMMINSIND": 300, "BSE": 250, "MCX": 250, "MAXHEALTH": 550,
    "TATACONSUM": 450, "APOLLOHOSP": 125, "DLF": 825, "HAL": 300, "VEDL": 1150,
    "POLYCAB": 125, "DIVISLAB": 200, "CIPLA": 650, "TRENT": 100, "SIEMENS": 125,
    "PFC": 1300, "RECLTD": 1000, "CANBK": 6750, "BANKBARODA": 2925, "PNB": 8000,
    "FEDERALBNK": 5000, "IDFCFIRSTB": 7500, "AUROPHARMA": 550, "LUPIN": 425, "BIOCON": 2500,
    "PERSISTENT": 125, "COFORGE": 150, "MPHASIS": 275, "TECHM": 600, "WIPRO": 1500,
    "CHOLAFIN": 625, "MUTHOOTFIN": 375, "MANAPPURAM": 3000, "ABCAPITAL": 3100, "LICHSGFIN": 1000,
    "AMBUJACEM": 900, "ACC": 300, "ULTRACEMCO": 100, "GRASIM": 250, "SHREECEM": 25,
    "TVSMOTOR": 350, "HEROMOTOCO": 150, "BAJAJ-AUTO": 75, "BHARATFORG": 500, "ASHOKLEY": 5000,
    "PIDILITIND": 250, "BERGEPAINT": 1100, "HAVELLS": 500, "VOLTAS": 600, "DIXON": 100
}

# Known Strike Intervals for Specific Stocks
NSE_STRIKE_INTERVALS = {
    "TATASTEEL": 2.5, "ITC": 5.0, "BEL": 5.0, "ONGC": 2.5, "COALINDIA": 5.0,
    "SBIN": 10.0, "TATAMOTORS": 10.0, "AXISBANK": 10.0, "ICICIBANK": 10.0, "HDFCBANK": 10.0,
    "INFY": 20.0, "RELIANCE": 20.0, "HINDALCO": 10.0, "SHRIRAMFIN": 20.0, "ADANIENT": 50.0,
    "BAJFINANCE": 20.0, "TCS": 20.0, "TITAN": 50.0, "EICHERMOT": 100.0, "INDIGO": 50.0,
    "MARUTI": 100.0, "APOLLOHOSP": 50.0, "LAURUSLABS": 10.0, "MAXHEALTH": 10.0
}


def get_last_thursday(year: int, month: int) -> datetime.date:
    """Calculates the last Thursday of a given year and month (NSE Expiry)."""
    if month == 12:
        next_month = datetime.date(year + 1, 1, 1)
    else:
        next_month = datetime.date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    offset = (last_day.weekday() - 3) % 7
    return last_day - timedelta(days=offset)


def get_active_monthly_expiry(ref_date: datetime.date = None) -> datetime.date:
    """
    Returns the active monthly F&O expiry date.
    Rolls over to next month if within 2 days of expiry to avoid physical delivery risk.
    """
    if ref_date is None:
        ref_date = datetime.date.today()

    exp_curr = get_last_thursday(ref_date.year, ref_date.month)
    
    # If expiry is in the past or within 2 days, roll over to next month
    if (exp_curr - ref_date).days <= 2:
        m = ref_date.month + 1 if ref_date.month < 12 else 1
        y = ref_date.year if ref_date.month < 12 else ref_date.year + 1
        return get_last_thursday(y, m)
    
    return exp_curr


def get_stock_lot_size(ticker: str, spot_price: float = 0.0) -> int:
    """Returns the official lot size for a stock, or computes a standard default."""
    clean_sym = ticker.upper().replace(".NS", "").replace("-EQ", "")
    if clean_sym in NSE_FO_LOT_SIZES:
        return NSE_FO_LOT_SIZES[clean_sym]
    
    # Standard default heuristic if unknown: contracts are sized ~Rs. 5,00,000 to 7,50,000 notional
    if spot_price > 0:
        target_notional = 600000.0
        est_lot = int(target_notional / spot_price)
        # Round to neat standard batches (25, 50, 100, 250, 500)
        if est_lot > 1000:
            return round(est_lot / 250) * 250
        elif est_lot > 200:
            return round(est_lot / 50) * 50
        elif est_lot > 50:
            return round(est_lot / 25) * 25
        else:
            return max(10, round(est_lot / 5) * 5)
            
    return 500


def get_strike_step(ticker: str, spot_price: float) -> float:
    """Returns the strike step interval for an equity option contract."""
    clean_sym = ticker.upper().replace(".NS", "").replace("-EQ", "")
    if clean_sym in NSE_STRIKE_INTERVALS:
        return NSE_STRIKE_INTERVALS[clean_sym]

    # Bracket fallback based on spot price
    if spot_price < 50:
        return 1.0
    elif spot_price < 100:
        return 2.5
    elif spot_price < 250:
        return 5.0
    elif spot_price < 500:
        return 10.0
    elif spot_price < 1000:
        return 20.0
    elif spot_price < 2500:
        return 50.0
    elif spot_price < 5000:
        return 100.0
    elif spot_price < 10000:
        return 100.0
    else:
        return 200.0


def calculate_atm_strike(spot_price: float, strike_step: float) -> float:
    """Rounds spot price to the nearest ATM strike."""
    return round(spot_price / strike_step) * strike_step


def recommend_option_contract(
    ticker: str,
    direction: str,
    spot_entry: float,
    spot_sl: float,
    spot_target1: float,
    spot_target2: float = None,
    demand_floor: float = None,
    supply_ceiling: float = None,
    ref_date: datetime.date = None
) -> dict:
    """
    Computes a complete, institutional Option Contract recommendation:
    - Primary Buying Contract (ATM CE for BUY / ATM PE for SELL)
    - Projected Option Entry / SL / Target based on ~0.50 ATM Delta
    - Lot Size, Total Risk in Rupees, Expected Profit in Rupees
    - Alternative Credit/Hedging Contract for Option Writers
    """
    clean_sym = ticker.upper().replace(".NS", "").replace("-EQ", "")
    direction_clean = "BUY" if direction.upper() in ["BUY", "LONG", "B/O CALL", "CALL"] else "SELL"
    
    expiry_date = get_active_monthly_expiry(ref_date)
    expiry_str = expiry_date.strftime("%d-%b").upper()
    expiry_full_str = expiry_date.strftime("%d-%b-%Y").upper()

    lot_size = get_stock_lot_size(clean_sym, spot_entry)
    step = get_strike_step(clean_sym, spot_entry)
    atm_strike = calculate_atm_strike(spot_entry, step)

    # Format strike string (e.g. 960 instead of 960.0 if integer)
    strike_fmt = f"{atm_strike:.0f}" if atm_strike.is_integer() else f"{atm_strike:.1f}"

    # ATM Options approximate Delta ~ 0.50
    delta = 0.50

    if direction_clean == "BUY":
        option_type = "CE"
        action_verb = "BUY CALL"
        contract_name = f"{clean_sym} {strike_fmt} CE"
        full_contract = f"{clean_sym} {expiry_str} {strike_fmt} CE"

        spot_gain_t1 = max(0.0, spot_target1 - spot_entry)
        spot_gain_t2 = max(0.0, (spot_target2 - spot_entry)) if spot_target2 else (spot_gain_t1 * 1.5)
        spot_risk = max(0.0, spot_entry - spot_sl)

        # Expected premium movements
        est_option_gain_t1 = round(spot_gain_t1 * delta, 2)
        est_option_gain_t2 = round(spot_gain_t2 * delta, 2)
        est_option_sl_pts = round(spot_risk * delta, 2)

        # Alternative Credit Spread for Option Sellers: Sell OTM Put below Demand 1
        hedge_strike = (demand_floor - step) if demand_floor else (spot_sl - step)
        hedge_strike_rounded = round(hedge_strike / step) * step
        hedge_fmt = f"{hedge_strike_rounded:.0f}" if hedge_strike_rounded.is_integer() else f"{hedge_strike_rounded:.1f}"
        alt_strategy = f"SELL {clean_sym} {hedge_fmt} PE (Credit buffer below Demand Floor)"

    else: # SELL / SHORT
        option_type = "PE"
        action_verb = "BUY PUT"
        contract_name = f"{clean_sym} {strike_fmt} PE"
        full_contract = f"{clean_sym} {expiry_str} {strike_fmt} PE"

        spot_gain_t1 = max(0.0, spot_entry - spot_target1)
        spot_gain_t2 = max(0.0, (spot_entry - spot_target2)) if spot_target2 else (spot_gain_t1 * 1.5)
        spot_risk = max(0.0, spot_sl - spot_entry)

        est_option_gain_t1 = round(spot_gain_t1 * delta, 2)
        est_option_gain_t2 = round(spot_gain_t2 * delta, 2)
        est_option_sl_pts = round(spot_risk * delta, 2)

        # Alternative Credit Spread for Option Sellers: Sell OTM Call above Supply 1
        hedge_strike = (supply_ceiling + step) if supply_ceiling else (spot_sl + step)
        hedge_strike_rounded = round(hedge_strike / step) * step
        hedge_fmt = f"{hedge_strike_rounded:.0f}" if hedge_strike_rounded.is_integer() else f"{hedge_strike_rounded:.1f}"
        alt_strategy = f"SELL {clean_sym} {hedge_fmt} CE (Credit buffer above Supply Ceiling)"

    # Total Rupees Impact per 1 Lot
    total_risk_rupees = round(est_option_sl_pts * lot_size, 2)
    total_profit_t1_rupees = round(est_option_gain_t1 * lot_size, 2)
    total_profit_t2_rupees = round(est_option_gain_t2 * lot_size, 2)

    return {
        "ticker": clean_sym,
        "direction": direction_clean,
        "option_type": option_type,
        "action": action_verb,
        "contract": contract_name,
        "full_contract": full_contract,
        "strike": atm_strike,
        "strike_str": strike_fmt,
        "expiry": expiry_full_str,
        "expiry_short": expiry_str,
        "lot_size": lot_size,
        "delta": delta,
        "est_gain_t1_pts": est_option_gain_t1,
        "est_gain_t2_pts": est_option_gain_t2,
        "est_sl_pts": est_option_sl_pts,
        "total_risk_rs": total_risk_rupees,
        "total_profit_t1_rs": total_profit_t1_rupees,
        "total_profit_t2_rs": total_profit_t2_rupees,
        "alt_option_selling": alt_strategy,
        "formatted_block": (
            f"🎯 *RECOMMENDED OPTION CONTRACT:*\n"
            f"• *Instrument:* `{full_contract}`\n"
            f"• *Strategy:* `{action_verb}` @ CMP (ATM Delta ~ 0.50)\n"
            f"• *Lot Size:* `{lot_size}` shares\n"
            f"• *Option Target 1:* `+{est_option_gain_t1:.2f} pts` (+Rs. {total_profit_t1_rupees:,.0f} / lot)\n"
            f"• *Option Target 2:* `+{est_option_gain_t2:.2f} pts` (+Rs. {total_profit_t2_rupees:,.0f} / lot)\n"
            f"• *Option Stop Loss:* `-{est_option_sl_pts:.2f} pts` (-Rs. {total_risk_rupees:,.0f} / lot)\n"
            f"• *Option Seller Alternative:* `{alt_strategy}`"
        )
    }


if __name__ == "__main__":
    # Test sample setups
    print("Testing Option Recommendation for SHRIRAMFIN (BUY):")
    sample_buy = recommend_option_contract(
        ticker="SHRIRAMFIN",
        direction="BUY",
        spot_entry=961.50,
        spot_sl=876.20,
        spot_target1=1132.10,
        spot_target2=1217.40,
        demand_floor=906.0
    )
    print(sample_buy["formatted_block"])
    print("\n" + "="*50 + "\n")
    print("Testing Option Recommendation for APOLLOHOSP (SELL / SHORT):")
    sample_sell = recommend_option_contract(
        ticker="APOLLOHOSP",
        direction="SELL",
        spot_entry=8696.0,
        spot_sl=8770.64,
        spot_target1=8381.71,
        supply_ceiling=8850.0
    )
    print(sample_sell["formatted_block"])
