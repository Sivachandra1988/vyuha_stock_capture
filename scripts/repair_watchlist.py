import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from db_connector import db
import db_storage

def repair_database():
    if db is None:
        print("MongoDB not connected.")
        return

    coll = db['watchlist']
    repaired_count = 0

    for doc in coll.find():
        ticker = doc.get('ticker')
        direction = doc.get('direction', '')
        is_short = 'SHORT' in direction.upper() or 'SELL' in direction.upper()
        try:
            e = float(doc.get('entry_price', 0) or 0)
        except Exception:
            continue

        p_t1 = float(doc.get('target1', 0) or 0)
        p_t2 = float(doc.get('target2', 0) or 0)
        sl = float(doc.get('sl', 0) or 0)
        proj = doc.get('projection', 'PROJ_NONE_0.0')

        needs_repair = False

        if is_short:
            if p_t1 > e and p_t2 < e:
                # p_t1 was Stop Loss, p_t2 was Target 1!
                sl = p_t1
                t1 = p_t2
                risk = sl - e
                t2 = round(e - 2.5 * risk, 2)
                needs_repair = True
            else:
                t1 = p_t1
                t2 = p_t2

            if 'PROJ_UP_' in proj:
                val = proj.replace('PROJ_UP_', '')
                proj = f'PROJ_DOWN_{val}'
                needs_repair = True
        else:
            if p_t1 < e and p_t2 > e:
                # p_t1 was Stop Loss, p_t2 was Target 1!
                sl = p_t1
                t1 = p_t2
                risk = e - sl
                t2 = round(e + 2.5 * risk, 2)
                needs_repair = True
            else:
                t1 = p_t1
                t2 = p_t2

            if 'PROJ_DOWN_' in proj:
                val = proj.replace('PROJ_DOWN_', '')
                proj = f'PROJ_UP_{val}'
                needs_repair = True

        if needs_repair or not doc.get('sl'):
            coll.update_one(
                {'_id': doc['_id']},
                {'$set': {
                    'sl': str(sl) if sl else str(round(e * (1.018 if is_short else 0.982), 2)),
                    'target1': str(t1),
                    'target2': str(t2),
                    'projection': proj
                }}
            )
            repaired_count += 1
            print(f"✓ Repaired {ticker}: Dir={direction}, Entry={e:.2f}, SL={sl:.2f}, T1={t1:.2f}, T2={t2:.2f}, Proj={proj}")

    print(f"\nTotal repaired documents in MongoDB: {repaired_count}")

def repair_watchlist_file():
    watchlist_file = os.path.join(BASE_DIR, "data", "watchlist.txt")
    if not os.path.exists(watchlist_file):
        return

    with open(watchlist_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    repaired_lines = []
    for line in lines:
        if not line.strip() or line.strip().startswith("#"):
            repaired_lines.append(line)
            continue

        parts = [p.strip() for p in line.split("|")]
        ticker = parts[0]
        strategy = parts[1] if len(parts) > 1 else "Swing"
        direction = parts[2] if len(parts) > 2 else "BUY"
        trade_type = parts[3] if len(parts) > 3 else "POSITIONAL"
        entry_str = parts[4] if len(parts) > 4 else "0.0"
        proj_str = parts[5] if len(parts) > 5 else "PROJ_NONE_0.0"

        try:
            entry = float(entry_str)
        except Exception:
            entry = 0.0

        is_short = 'SHORT' in direction.upper() or 'SELL' in direction.upper()

        if is_short and 'PROJ_UP_' in proj_str:
            val = proj_str.replace('PROJ_UP_', '')
            proj_str = f'PROJ_DOWN_{val}'
        elif not is_short and 'PROJ_DOWN_' in proj_str:
            val = proj_str.replace('PROJ_DOWN_', '')
            proj_str = f'PROJ_UP_{val}'

        p6 = float(parts[6]) if len(parts) > 6 and parts[6] else None
        p7 = float(parts[7]) if len(parts) > 7 and parts[7] else None
        p8 = float(parts[8]) if len(parts) > 8 and parts[8] else None

        sl = p6
        t1 = p7
        t2 = p8

        if len(parts) == 8 and entry > 0 and p6 is not None and p7 is not None:
            if is_short and p6 > entry and p7 < entry:
                sl = p6
                t1 = p7
                t2 = round(entry - 2.5 * (sl - entry), 2)
            elif not is_short and p6 < entry and p7 > entry:
                sl = p6
                t1 = p7
                t2 = round(entry + 2.5 * (entry - sl), 2)
        elif len(parts) >= 9 and entry > 0 and p6 is not None and p7 is not None:
            # Already 9 parts: parts[6]=sl, parts[7]=t1, parts[8]=t2
            sl = p6
            t1 = p7
            t2 = p8

        if not sl and entry > 0:
            sl = round(entry * (1.018 if is_short else 0.982), 2)
        if not t1 and entry > 0:
            t1 = round(entry * (0.98 if is_short else 1.02), 2)
        if not t2 and entry > 0:
            t2 = round(entry * (0.96 if is_short else 1.04), 2)

        repaired_lines.append(f"{ticker} | {strategy} | {direction} | {trade_type} | {entry:.2f} | {proj_str} | {sl:.2f} | {t1:.2f} | {t2:.2f}\n")

    with open(watchlist_file, "w", encoding="utf-8") as f:
        f.writelines(repaired_lines)
    print("✓ Repaired data/watchlist.txt successfully.")

if __name__ == "__main__":
    repair_database()
    repair_watchlist_file()
