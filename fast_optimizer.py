"""
High-Speed Precomputed VYUHA Strategy Optimizer.
Precomputes all MTF zones and candle features ONCE in parallel (takes ~10s),
then tests multiple execution & risk-reward scenarios in pure memory (< 1s).
"""

import os, sys, glob
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from scanner import vyuha

CACHE_DIR = os.path.join(BASE_DIR, "data", "backtest_cache")

def precompute_bar_features_for_ticker(ticker_path):
    ticker = os.path.basename(ticker_path).replace(".parquet", "")
    df = pd.read_parquet(ticker_path)
    df = vyuha.clean_dataframe(df)
    if len(df) < 150:
        return ticker, []

    df['ATR_14'] = vyuha.calculate_atr(df, 14)
    df['EMA_9'] = vyuha.calculate_ema(df['Close'], 9)
    df['EMA_21'] = vyuha.calculate_ema(df['Close'], 21)
    df['SMA_50'] = df['Close'].rolling(50).mean()
    df['SMA_200'] = df['Close'].rolling(200).mean()
    df['Vol_SMA_20'] = df['Volume'].rolling(20).mean()

    precomputed_bars = []

    for i in range(120, len(df)):
        curr_bar = df.iloc[i]
        prev_bar = df.iloc[i - 1]
        c_date = df.index[i].strftime("%Y-%m-%d")
        c_open = float(curr_bar['Open'])
        c_high = float(curr_bar['High'])
        c_low = float(curr_bar['Low'])
        c_close = float(curr_bar['Close'])
        atr_val = float(curr_bar['ATR_14'])

        if atr_val <= 0:
            continue

        hist_df = df.iloc[:i]
        zones = vyuha.calculate_vyuha_zones(hist_df, c_close, atr_val)
        if not zones or zones['is_squeeze']:
            continue

        d1 = zones['demand_1']
        s1 = zones['supply_1']

        is_bull_trap, is_bear_trap, lower_wick_pct, upper_wick_pct = vyuha.evaluate_anti_trap_gates(curr_bar)

        sma50 = float(curr_bar['SMA_50']) if not pd.isna(curr_bar['SMA_50']) else c_close
        sma200 = float(curr_bar['SMA_200']) if not pd.isna(curr_bar['SMA_200']) else sma50
        vol = float(curr_bar['Volume'])
        vol_sma = float(curr_bar['Vol_SMA_20']) if not pd.isna(curr_bar['Vol_SMA_20']) else vol

        precomputed_bars.append({
            "date": c_date,
            "open": c_open,
            "high": c_high,
            "low": c_low,
            "close": c_close,
            "atr": atr_val,
            "d1_top": d1['top'],
            "d1_bot": d1['bot'],
            "dem_sl": zones['dem_retrace']['sl'],
            "s1_top": s1['top'],
            "s1_bot": s1['bot'],
            "sup_sl": zones['sup_retrace']['sl'],
            "lower_wick_pct": lower_wick_pct,
            "upper_wick_pct": upper_wick_pct,
            "is_bull_trap": is_bull_trap,
            "is_bear_trap": is_bear_trap,
            "sma50": sma50,
            "sma200": sma200,
            "vol": vol,
            "vol_sma": vol_sma
        })

    return ticker, precomputed_bars


def build_precomputed_dataset():
    files = glob.glob(os.path.join(CACHE_DIR, "*.parquet"))
    dataset = {}
    print(f"⚡ Precomputing zones for {len(files)} tickers in parallel (ThreadPool 8)...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = ex.map(precompute_bar_features_for_ticker, files)
        for ticker, bars in results:
            if bars:
                dataset[ticker] = bars
    print(f"✓ Precomputation complete! Ready for instantaneous simulation.\n")
    return dataset


def run_scenario(dataset, params):
    mode = params.get('mode', 'BOTH')
    rr_target = params.get('rr_target', 2.0)
    use_200_sma = params.get('use_200_sma', True)
    min_wick = params.get('min_wick', 0.28)
    vol_mult = params.get('vol_mult', 0.9)
    max_holding = params.get('max_holding', 15)

    all_trades = []

    for ticker, bars in dataset.items():
        active = None

        for bar in bars:
            c_high = bar['high']
            c_low = bar['low']
            c_close = bar['close']
            c_open = bar['open']
            c_date = bar['date']
            atr_val = bar['atr']

            # 1. Manage Active Trade
            if active is not None:
                active['bars'] += 1
                entry_p = active['entry']
                risk = active['risk']
                t1 = active['t1']
                dir_ = active['dir']

                closed = False
                exit_p = None
                exit_r = None

                if dir_ == "LONG":
                    if c_high >= (entry_p + risk) and not active['be']:
                        active['sl'] = entry_p
                        active['be'] = True

                    if c_high >= t1:
                        closed = True
                        exit_p = t1
                        exit_r = "TARGET"
                    elif c_low <= active['sl']:
                        closed = True
                        exit_p = active['sl']
                        exit_r = "BE" if active['be'] else "SL"
                    elif active['bars'] >= max_holding:
                        closed = True
                        exit_p = c_close
                        exit_r = "TIME"

                elif dir_ == "SHORT":
                    if c_low <= (entry_p - risk) and not active['be']:
                        active['sl'] = entry_p
                        active['be'] = True

                    if c_low <= t1:
                        closed = True
                        exit_p = t1
                        exit_r = "TARGET"
                    elif c_high >= active['sl']:
                        closed = True
                        exit_p = active['sl']
                        exit_r = "BE" if active['be'] else "SL"
                    elif active['bars'] >= max_holding:
                        closed = True
                        exit_p = c_close
                        exit_r = "TIME"

                if closed:
                    pnl_pts = (exit_p - entry_p) if dir_ == "LONG" else (entry_p - exit_p)
                    pnl_pct = (pnl_pts / entry_p) * 100.0
                    outcome = "WIN" if pnl_pts > (0.1 * risk) else ("BE" if abs(pnl_pts) <= (0.1 * risk) else "LOSS")
                    all_trades.append({
                        "ticker": ticker,
                        "dir": dir_,
                        "outcome": outcome,
                        "pnl_pct": pnl_pct,
                        "reason": exit_r
                    })
                    active = None
                    continue

            # 2. Check Entry Signal
            if active is None:
                sma50 = bar['sma50']
                sma200 = bar['sma200']
                vol = bar['vol']
                vol_sma = bar['vol_sma']
                has_vol = vol >= (vol_mult * vol_sma)

                if use_200_sma:
                    bull_regime = (c_close >= sma200) and (sma50 >= sma200)
                    bear_regime = (c_close <= sma200) and (sma50 <= sma200)
                else:
                    bull_regime = c_close >= sma50
                    bear_regime = c_close <= sma50

                # LONG check
                in_demand = (c_low <= bar['d1_top'] and c_close >= bar['d1_bot'])
                demand_rej = bar['lower_wick_pct'] >= min_wick and c_close > c_open

                if bull_regime and has_vol and in_demand and demand_rej and not bar['is_bull_trap']:
                    entry = c_close
                    sl = bar['dem_sl']
                    risk = entry - sl
                    if risk <= 0 or risk > (2.5 * atr_val):
                        sl = entry - (1.2 * atr_val)
                        risk = entry - sl

                    active = {
                        "dir": "LONG",
                        "entry": entry,
                        "sl": sl,
                        "t1": entry + (rr_target * risk),
                        "risk": risk,
                        "bars": 0,
                        "be": False
                    }
                    continue

                # SHORT check
                if mode == 'BOTH':
                    in_supply = (c_high >= bar['s1_bot'] and c_close <= bar['s1_top'])
                    supply_rej = bar['upper_wick_pct'] >= min_wick and c_close < c_open

                    if bear_regime and has_vol and in_supply and supply_rej and not bar['is_bear_trap']:
                        entry = c_close
                        sl = bar['sup_sl']
                        risk = sl - entry
                        if risk <= 0 or risk > (2.5 * atr_val):
                            sl = entry + (1.2 * atr_val)
                            risk = sl - entry

                        active = {
                            "dir": "SHORT",
                            "entry": entry,
                            "sl": sl,
                            "t1": entry - (rr_target * risk),
                            "risk": risk,
                            "bars": 0,
                            "be": False
                        }
                        continue

    if not all_trades:
        return {"total": 0, "win_rate": 0, "pf": 0, "pnl": 0}

    w = len([t for t in all_trades if t['outcome'] == 'WIN'])
    l = len([t for t in all_trades if t['outcome'] == 'LOSS'])
    be = len([t for t in all_trades if t['outcome'] == 'BE'])
    wr = (w / (w + l) * 100.0) if (w + l) > 0 else 0.0

    pnl = sum(t['pnl_pct'] for t in all_trades)
    gw = sum(t['pnl_pct'] for t in all_trades if t['outcome'] == 'WIN')
    gl = abs(sum(t['pnl_pct'] for t in all_trades if t['outcome'] == 'LOSS')) or 0.001
    pf = gw / gl

    return {
        "total": len(all_trades),
        "wins": w,
        "losses": l,
        "be": be,
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "pnl": round(pnl, 1)
    }

if __name__ == "__main__":
    dataset = build_precomputed_dataset()

    grid = [
        {"name": "1. Both Sides (50 SMA, 2.0R)", "mode": "BOTH", "use_200_sma": False, "rr_target": 2.0, "min_wick": 0.28, "vol_mult": 0.85},
        {"name": "2. Both Sides (200 SMA, 2.0R)", "mode": "BOTH", "use_200_sma": True, "rr_target": 2.0, "min_wick": 0.28, "vol_mult": 0.90},
        {"name": "3. Both Sides (200 SMA, 1.5R)", "mode": "BOTH", "use_200_sma": True, "rr_target": 1.5, "min_wick": 0.28, "vol_mult": 0.90},
        {"name": "4. Long-Only (200 SMA, 2.0R)", "mode": "LONG_ONLY", "use_200_sma": True, "rr_target": 2.0, "min_wick": 0.28, "vol_mult": 0.90},
        {"name": "5. Long-Only (200 SMA, 1.5R)", "mode": "LONG_ONLY", "use_200_sma": True, "rr_target": 1.5, "min_wick": 0.28, "vol_mult": 0.90},
        {"name": "6. High-Conviction Long (200 SMA + Wick >= 32%, 1.8R)", "mode": "LONG_ONLY", "use_200_sma": True, "rr_target": 1.8, "min_wick": 0.32, "vol_mult": 1.00},
        {"name": "7. High-Conviction Long (200 SMA + Wick >= 30%, 1.6R)", "mode": "LONG_ONLY", "use_200_sma": True, "rr_target": 1.6, "min_wick": 0.30, "vol_mult": 0.95},
        {"name": "8. Golden Pullback (200 SMA + Wick >= 35%, 1.5R)", "mode": "LONG_ONLY", "use_200_sma": True, "rr_target": 1.5, "min_wick": 0.35, "vol_mult": 1.00},
    ]

    print(f"{'Configuration':<48} {'Trades':<8} {'Wins':<6} {'Loss':<6} {'BE':<6} {'Win Rate':<10} {'PF':<7} {'PnL %':<10}")
    print("=" * 105)

    for g in grid:
        res = run_scenario(dataset, g)
        print(f"{g['name']:<48} {res['total']:<8} {res['wins']:<6} {res['losses']:<6} {res['be']:<6} {res['win_rate']:>5.1f}%     {res['profit_factor']:<7.2f} {res['pnl']:>+7.1f}%")

