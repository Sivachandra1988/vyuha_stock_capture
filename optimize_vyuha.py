"""
Focused Grid Optimizer for VYUHA & Price Action Rules
"""
import os
import sys
import itertools
import time

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr.encoding != "utf-8":
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from backtest_vyuha_fast import run_strategy_backtest

# Focused hyperparameter grid: 72 high-impact configurations
grid = {
    "rr_target": [1.8, 2.0, 2.2],
    "be_trigger_rr": [0.8, 1.0],
    "min_atr_risk": [1.2, 1.5],
    "max_atr_risk": [2.5],
    "wick_thresh": [0.28, 0.35],
    "trap_thresh": [0.20, 0.25],
    "use_200_sma": [True],
    "include_momentum": [True],
    "max_holding": [15, 20]
}

keys, values = zip(*grid.items())
permutations_dicts = [dict(zip(keys, v)) for v in itertools.product(*values)]
print(f"Total permutations to test: {len(permutations_dicts)}", flush=True)

results = []
t0 = time.time()

for idx, p in enumerate(permutations_dicts):
    res = run_strategy_backtest(p)
    if res and res['total_trades'] >= 50:
        results.append({
            "pf": res['profit_factor'],
            "net": res['net_return_pct'],
            "wr": res['win_rate'],
            "trades": res['total_trades'],
            "wins": res['wins'],
            "losses": res['losses'],
            "bes": res['break_evens'],
            "avg_win": res['avg_win_pct'],
            "avg_loss": res['avg_loss_pct'],
            "realized_rr": res['realized_rr'],
            "dd": res['max_drawdown'],
            "params": p
        })
    if (idx + 1) % 12 == 0:
        print(f"  Processed {idx + 1}/{len(permutations_dicts)} in {time.time()-t0:.1f}s...", flush=True)

print(f"\nTested {len(permutations_dicts)} combinations in {time.time()-t0:.2f}s.", flush=True)

filtered = [r for r in results if r['trades'] >= 60]
filtered.sort(key=lambda x: (x['pf'], x['net']), reverse=True)

print("\n" + "=" * 80)
print(f"🏆 TOP 10 PROFITABLE CONFIGURATIONS (Sorted by Profit Factor)")
print("=" * 80)
for i, r in enumerate(filtered[:10]):
    p = r['params']
    print(f"#{i+1:2d} | PF: {r['pf']:4.2f} | Net: +{r['net']:6.1f}% | WR: {r['wr']:4.1f}% | Trades: {r['trades']} (W:{r['wins']} L:{r['losses']} BE:{r['bes']}) | RR: 1:{r['realized_rr']:3.2f} | DD: {r['dd']:4.1f}%")
    print(f"     Params: R:R={p['rr_target']}, BE_Trigger={p['be_trigger_rr']}, MinATR={p['min_atr_risk']}, Wick={p['wick_thresh']}, Trap={p['trap_thresh']}, MaxHold={p['max_holding']}\n")
