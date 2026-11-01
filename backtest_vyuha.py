"""
=============================================================================
🏛️ UMX VYUHA — Institutional MTF Confluence Strategy Backtest Engine
Backtests the institutional confluence, zone bounce/rejection, 
anti-trap wicks, AVWAP diamond, and 1:2 R:R trailing break-even trade engine.
=============================================================================
"""

import os
import sys
import json
import numpy as np
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Windows encoding safeguard
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from scanner import core, vyuha

def backtest_ticker_vyuha(ticker, period="2y"):
    """
    Simulates VYUHA Institutional Confluence trading on historical daily bars.
    Strictly prevents lookahead bias: zones and levels are evaluated on rolling lookback windows.
    """
    trades = []
    try:
        parquet_path = os.path.join(DATA_DIR, "backtest_cache", f"{ticker}.parquet")
        if os.path.exists(parquet_path):
            df = pd.read_parquet(parquet_path)
        else:
            df = yf.download(f"{ticker}.NS", period=period, interval="1d", progress=False, auto_adjust=True)

        if df.empty or len(df) < 120:
            return []

        df = vyuha.clean_dataframe(df)
        df['ATR_14'] = vyuha.calculate_atr(df, 14)
        df['EMA_9'] = vyuha.calculate_ema(df['Close'], 9)
        df['EMA_21'] = vyuha.calculate_ema(df['Close'], 21)
        df['SMA_50'] = df['Close'].rolling(50).mean()
        df['SMA_200'] = df['Close'].rolling(200).mean()
        df['Vol_SMA_20'] = df['Volume'].rolling(20).mean()

        active_trade = None
        max_holding_bars = 15

        # Need at least 90 bars of history before placing the first trade
        for i in range(90, len(df)):
            curr_bar = df.iloc[i]
            prev_bar = df.iloc[i - 1]
            c_date = df.index[i].strftime("%Y-%m-%d")
            c_open = float(curr_bar['Open'])
            c_high = float(curr_bar['High'])
            c_low = float(curr_bar['Low'])
            c_close = float(curr_bar['Close'])
            atr_val = float(curr_bar['ATR_14'])

            # -----------------------------------------------------------------
            # 1. TRADE MANAGEMENT (If already in an active trade)
            # -----------------------------------------------------------------
            if active_trade is not None:
                active_trade['holding_bars'] += 1
                entry_p = active_trade['entry_price']
                risk = active_trade['risk']
                sl_p = active_trade['stop_loss']
                t1_p = active_trade['target_1']
                direction = active_trade['direction']

                trade_closed = False
                exit_price = None
                exit_reason = None

                if direction == "LONG":
                    # Check 1:1 R:R Trailing to Break-Even
                    be_threshold = entry_p + risk
                    if c_high >= be_threshold and not active_trade['is_be']:
                        active_trade['stop_loss'] = entry_p
                        active_trade['is_be'] = True

                    # Check Target 1 (1:2 R:R)
                    if c_high >= t1_p:
                        trade_closed = True
                        exit_price = t1_p
                        exit_reason = "TARGET_1 (1:2 R:R)"
                    # Check Stop Loss
                    elif c_low <= active_trade['stop_loss']:
                        trade_closed = True
                        exit_price = active_trade['stop_loss']
                        exit_reason = "BREAK_EVEN" if active_trade['is_be'] else "STOP_LOSS"
                    # Check Time-based Exit (Max Holding Period)
                    elif active_trade['holding_bars'] >= max_holding_bars:
                        trade_closed = True
                        exit_price = c_close
                        exit_reason = "TIME_EXPIRY"

                elif direction == "SHORT":
                    # Check 1:1 R:R Trailing to Break-Even
                    be_threshold = entry_p - risk
                    if c_low <= be_threshold and not active_trade['is_be']:
                        active_trade['stop_loss'] = entry_p
                        active_trade['is_be'] = True

                    # Check Target 1 (1:2 R:R)
                    if c_low <= t1_p:
                        trade_closed = True
                        exit_price = t1_p
                        exit_reason = "TARGET_1 (1:2 R:R)"
                    # Check Stop Loss
                    elif c_high >= active_trade['stop_loss']:
                        trade_closed = True
                        exit_price = active_trade['stop_loss']
                        exit_reason = "BREAK_EVEN" if active_trade['is_be'] else "STOP_LOSS"
                    # Check Time-based Exit
                    elif active_trade['holding_bars'] >= max_holding_bars:
                        trade_closed = True
                        exit_price = c_close
                        exit_reason = "TIME_EXPIRY"

                if trade_closed:
                    pnl_pts = (exit_price - entry_p) if direction == "LONG" else (entry_p - exit_price)
                    pnl_pct = (pnl_pts / entry_p) * 100.0

                    outcome = "WIN" if pnl_pts > (0.1 * risk) else ("BE" if abs(pnl_pts) <= (0.1 * risk) else "LOSS")

                    trades.append({
                        "ticker": ticker,
                        "direction": direction,
                        "entry_date": active_trade['entry_date'],
                        "entry_price": round(entry_p, 2),
                        "exit_date": c_date,
                        "exit_price": round(exit_price, 2),
                        "exit_reason": exit_reason,
                        "outcome": outcome,
                        "pnl_pts": round(pnl_pts, 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "holding_bars": active_trade['holding_bars']
                    })
                    active_trade = None
                    continue

            # -----------------------------------------------------------------
            # 2. SIGNAL GENERATION (Strictly historical window df.iloc[:i])
            # -----------------------------------------------------------------
            if active_trade is None and atr_val > 0:
                hist_df = df.iloc[:i]
                zones = vyuha.calculate_vyuha_zones(hist_df, c_close, atr_val)
                if not zones or zones['is_squeeze']:
                    continue

                d1 = zones['demand_1']
                s1 = zones['supply_1']

                is_bull_trap, is_bear_trap, lower_wick_pct, upper_wick_pct = vyuha.evaluate_anti_trap_gates(curr_bar)

                # AVWAP slope over recent 15 bars
                recent_slice = hist_df.tail(15)
                avwap_val, avwap_slope, is_green_diamond = vyuha.calculate_zone_avwap(recent_slice)

                ema9 = float(curr_bar['EMA_9'])
                ema21 = float(curr_bar['EMA_21'])
                ema9_p = float(prev_bar['EMA_9'])
                ema21_p = float(prev_bar['EMA_21'])

                # Gate 1: HTF Structure Bias (50 SMA) & Gate 4: Volume
                sma50 = float(curr_bar['SMA_50']) if not pd.isna(curr_bar['SMA_50']) else c_close
                vol = float(curr_bar['Volume'])
                vol_sma = float(curr_bar['Vol_SMA_20']) if not pd.isna(curr_bar['Vol_SMA_20']) else vol
                has_vol = vol >= (0.85 * vol_sma)

                # Condition A: Demand Bounce Long (HTF Bullish)
                in_demand = (c_low <= d1['top'] and c_close >= d1['bot'])
                demand_rejection = lower_wick_pct >= 0.28 and c_close > c_open
                trend_start_buy = (ema9_p <= ema21_p and ema9 > ema21) and (c_close >= d1['bot'])
                htf_bull_allowed = c_close >= sma50

                if htf_bull_allowed and has_vol and ((in_demand and demand_rejection and not is_bull_trap) or trend_start_buy):
                    entry = c_close
                    sl = zones['dem_retrace']['sl']
                    risk = entry - sl
                    if risk <= 0 or risk > (2.5 * atr_val):
                        sl = entry - (1.2 * atr_val)
                        risk = entry - sl

                    t1 = entry + (2.0 * risk)
                    t2 = entry + (3.0 * risk)

                    active_trade = {
                        "ticker": ticker,
                        "direction": "LONG",
                        "entry_date": c_date,
                        "entry_price": entry,
                        "stop_loss": sl,
                        "target_1": t1,
                        "target_2": t2,
                        "risk": risk,
                        "holding_bars": 0,
                        "is_be": False
                    }
                    continue

                # Condition B: Supply Rejection Short (HTF Bearish)
                in_supply = (c_high >= s1['bot'] and c_close <= s1['top'])
                supply_rejection = upper_wick_pct >= 0.28 and c_close < c_open
                trend_start_sell = (ema9_p >= ema21_p and ema9 < ema21) and (c_close <= s1['top'])
                htf_bear_allowed = c_close <= sma50

                if htf_bear_allowed and has_vol and ((in_supply and supply_rejection and not is_bear_trap) or trend_start_sell):
                    entry = c_close
                    sl = zones['sup_retrace']['sl']
                    risk = sl - entry
                    if risk <= 0 or risk > (2.5 * atr_val):
                        sl = entry + (1.2 * atr_val)
                        risk = sl - entry

                    t1 = entry - (2.0 * risk)
                    t2 = entry - (3.0 * risk)

                    active_trade = {
                        "ticker": ticker,
                        "direction": "SHORT",
                        "entry_date": c_date,
                        "entry_price": entry,
                        "stop_loss": sl,
                        "target_1": t1,
                        "target_2": t2,
                        "risk": risk,
                        "holding_bars": 0,
                        "is_be": False
                    }
                    continue

    except Exception as e:
        pass

    return trades


def run_full_vyuha_backtest(tickers=None, max_workers=8):
    """Runs backtest across the tickers universe and aggregates metrics."""
    if not tickers:
        tickers = core.get_dynamic_tickers()
        if not tickers:
            tickers = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "LT", "BAJFINANCE"]

    print(f"\n==========================================================================")
    print(f"🏛️  UMX VYUHA INSTITUTIONAL CONFLUENCE BACKTEST ENGINE")
    print(f"Testing Universe: {len(tickers)} Liquid Stocks | Period: 2 Years Daily Bars")
    print(f"Rules: Zone 1 MTF Confluence | Anti-Trap Wicks | AVWAP | 1:2 R:R Trailing BE")
    print(f"==========================================================================\n")

    all_trades = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(backtest_ticker_vyuha, ticker): ticker for ticker in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                t_list = future.result()
                if t_list:
                    all_trades.extend(t_list)
                    w = len([t for t in t_list if t['outcome'] == 'WIN'])
                    l = len([t for t in t_list if t['outcome'] == 'LOSS'])
                    be = len([t for t in t_list if t['outcome'] == 'BE'])
                    total = len(t_list)
                    wr = (w / (w + l) * 100.0) if (w + l) > 0 else 0
                    print(f"  ✓ {ticker:<12} : {total:2d} Trades | {w:2d} Wins | {l:2d} Losses | {be:2d} BE | Win Rate: {wr:5.1f}%")
            except Exception as e:
                print(f"  ✗ {ticker:<12} : Error ({e})")

    if not all_trades:
        print("\n[INFO] No trades generated across the universe.")
        return

    # Calculate overall metrics
    total_trades = len(all_trades)
    wins = [t for t in all_trades if t['outcome'] == 'WIN']
    losses = [t for t in all_trades if t['outcome'] == 'LOSS']
    break_evens = [t for t in all_trades if t['outcome'] == 'BE']

    win_count = len(wins)
    loss_count = len(losses)
    be_count = len(break_evens)

    win_rate = (win_count / (win_count + loss_count) * 100.0) if (win_count + loss_count) > 0 else 0.0
    overall_win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

    gross_profit = sum(t['pnl_pct'] for t in wins)
    gross_loss = abs(sum(t['pnl_pct'] for t in losses)) if losses else 0.001
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else gross_profit

    net_return_pct = sum(t['pnl_pct'] for t in all_trades)
    avg_win_pct = (gross_profit / win_count) if win_count > 0 else 0.0
    avg_loss_pct = (gross_loss / loss_count) if loss_count > 0 else 0.0
    risk_reward_realized = (avg_win_pct / avg_loss_pct) if avg_loss_pct > 0 else 0.0

    # Drawdown calculation
    cumulative_returns = np.cumsum([t['pnl_pct'] for t in all_trades])
    peak = np.maximum.accumulate(cumulative_returns)
    drawdowns = peak - cumulative_returns
    max_drawdown = np.max(drawdowns) if len(drawdowns) > 0 else 0.0

    print("\n" + "=" * 74)
    print(" 📊 AUDITED PERFORMANCE AUDIT MATRIX — UMX VYUHA CONFLUENCE SYSTEM")
    print("=" * 74)
    print(f" Total Trades Executed  : {total_trades}")
    print(f" Winning Trades (🎯 TP1): {win_count}  ({win_rate:.1f}% Win Rate excluding BE)")
    print(f" Losing Trades  (🛑 SL) : {loss_count}")
    print(f" Break-Even Trades (🛡️) : {be_count}  ({(be_count / total_trades * 100.0):.1f}% Protected via 1:1 Trailing)")
    print(f" Profit Factor (PF)     : {profit_factor:.2f}")
    print(f" Total Cumulative PnL   : +{net_return_pct:.2f}%")
    print(f" Realized R:R Ratio     : 1:{risk_reward_realized:.2f} (Avg Win: +{avg_win_pct:.2f}% | Avg Loss: -{avg_loss_pct:.2f}%)")
    print(f" Maximum Drawdown (DD)  : -{max_drawdown:.2f}%")
    print("=" * 74)

    # Print sample recent trades
    print("\n📋 RECENT AUDITED TRADES AUDIT LOG (Sample of Last 15 Trades):")
    print(f"{'Ticker':<10} {'Dir':<6} {'Entry Date':<11} {'Entry':<8} {'Exit Date':<11} {'Exit':<8} {'Outcome':<8} {'PnL %':<8} {'Reason'}")
    print("-" * 88)
    for t in all_trades[-15:]:
        sym_dir = "🟢 LONG" if t['direction'] == "LONG" else "🔴 SHORT"
        out_badge = "✅ WIN" if t['outcome'] == "WIN" else ("🛡️ BE" if t['outcome'] == "BE" else "❌ LOSS")
        print(f"{t['ticker']:<10} {sym_dir:<6} {t['entry_date']:<11} {t['entry_price']:<8.2f} {t['exit_date']:<11} {t['exit_price']:<8.2f} {out_badge:<8} {t['pnl_pct']:>+6.2f}%  {t['exit_reason']}")
    print("-" * 88)

    # Save to file
    out_file = os.path.join(DATA_DIR, "vyuha_backtest_results.json")
    with open(out_file, "w") as f:
        json.dump({
            "metrics": {
                "total_trades": total_trades,
                "wins": win_count,
                "losses": loss_count,
                "break_evens": be_count,
                "win_rate": round(win_rate, 2),
                "profit_factor": round(profit_factor, 2),
                "net_return_pct": round(net_return_pct, 2),
                "avg_win_pct": round(avg_win_pct, 2),
                "avg_loss_pct": round(avg_loss_pct, 2),
                "realized_rr": round(risk_reward_realized, 2),
                "max_drawdown": round(max_drawdown, 2)
            },
            "trades": all_trades
        }, f, indent=4)
    print(f"\n💾 Full backtest logs and trade-by-trade breakdown saved to: {out_file}\n")

if __name__ == "__main__":
    run_full_vyuha_backtest()

