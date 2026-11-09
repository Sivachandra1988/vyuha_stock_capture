import sys
sys.path.extend(['.', 'src'])
import yfinance as yf
from scanner import vyuha_bo_workflow

tickers = ['WIPRO', 'TRENT', 'BHARTIARTL', 'MAXHEALTH', 'HINDALCO', 'ONGC', 'GRASIM']
for sym in tickers:
    df_i = yf.download(sym + '.NS', period='5d', interval='15m', progress=False, auto_adjust=True)
    df_d = yf.download(sym + '.NS', period='3mo', interval='1d', progress=False, auto_adjust=True)
    res = vyuha_bo_workflow.validate_vyuha_bo_setup(sym, df_i, df_d)
    valid = res["is_valid"]
    print(f"{sym:12s}: is_valid={valid}")
    if valid:
        print(f"   -> Direction: {res['direction']} | Setup: {res['setup_type']} | Status: {res['bo_status']}")
    else:
        print(f"   -> Rejection: {res.get('rejection_reason')}")
