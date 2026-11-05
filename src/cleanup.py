import os
import sys
import shutil
import glob
import tempfile

# Force UTF-8 stdout reconfiguration if available
if hasattr(sys.stdout, "reconfigure") and sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKTEST_CACHE_DIR = os.path.join(DATA_DIR, "backtest_cache")

def cleanup_all_temp_data(verbose=True):
    """
    Cleans up all temporary files, SQLite caches, and disk cache on engine startup.
    - py-yfinance SQLite cache files (cookies.db, tkr-tz.db, *.sqlite)
    - Project SQLite files (*.db, *.sqlite, *.sqlite3, *.db-wal, *.db-shm)
    - Backtest parquet cache (data/backtest_cache/)
    - Python bytecode cache (__pycache__, *.pyc, *.pyo)
    - System temporary files (*.tmp, *.temp)
    """
    removed_files = 0
    removed_bytes = 0
    removed_dirs = 0

    # 1. Clean py-yfinance SQLite cache directories (Linux/Termux ~/.cache/py-yfinance & Windows %LOCALAPPDATA%/py-yfinance)
    yfinance_cache_dirs = [
        os.path.expanduser("~/.cache/py-yfinance"),
        os.path.expanduser("~/.cache/yfinance"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "py-yfinance"),
        os.path.join(tempfile.gettempdir(), "py-yfinance")
    ]

    for cdir in yfinance_cache_dirs:
        if cdir and os.path.exists(cdir):
            try:
                for entry in os.listdir(cdir):
                    fpath = os.path.join(cdir, entry)
                    try:
                        if os.path.isfile(fpath) or os.path.islink(fpath):
                            sz = os.path.getsize(fpath)
                            os.remove(fpath)
                            removed_files += 1
                            removed_bytes += sz
                        elif os.path.isdir(fpath):
                            shutil.rmtree(fpath, ignore_errors=True)
                            removed_dirs += 1
                    except Exception:
                        pass
            except Exception:
                pass

    # 2. Clean workspace SQLite and journal/temp files
    sqlite_extensions = (
        ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".db-journal",
        ".tmp", ".temp", ".part"
    )
    for root, dirs, files in os.walk(BASE_DIR):
        # Don't touch git metadata
        if ".git" in root or ".venv" in root or "venv" in root:
            continue
        for fname in files:
            lower = fname.lower()
            # Clean temporary SQLite and scratch files (avoid removing essential json/csv/txt)
            if lower.endswith(sqlite_extensions) or (lower.endswith(".db") and ("cache" in lower or "temp" in lower or "test" in lower or "cookie" in lower)):
                fpath = os.path.join(root, fname)
                try:
                    sz = os.path.getsize(fpath)
                    os.remove(fpath)
                    removed_files += 1
                    removed_bytes += sz
                except Exception:
                    pass

    # 3. Clean Backtest Parquet Cache (data/backtest_cache/*.parquet)
    if os.path.exists(BACKTEST_CACHE_DIR):
        try:
            for fname in os.listdir(BACKTEST_CACHE_DIR):
                fpath = os.path.join(BACKTEST_CACHE_DIR, fname)
                try:
                    if os.path.isfile(fpath):
                        sz = os.path.getsize(fpath)
                        os.remove(fpath)
                        removed_files += 1
                        removed_bytes += sz
                except Exception:
                    pass
        except Exception:
            pass

    # 4. Clean Python __pycache__ directories and .pyc/.pyo files
    for root, dirs, files in os.walk(BASE_DIR, topdown=False):
        if ".git" in root or ".venv" in root or "venv" in root:
            continue
        for fname in files:
            if fname.lower().endswith((".pyc", ".pyo")):
                fpath = os.path.join(root, fname)
                try:
                    sz = os.path.getsize(fpath)
                    os.remove(fpath)
                    removed_files += 1
                    removed_bytes += sz
                except Exception:
                    pass
        for dname in dirs:
            if dname == "__pycache__":
                dpath = os.path.join(root, dname)
                try:
                    shutil.rmtree(dpath, ignore_errors=True)
                    removed_dirs += 1
                except Exception:
                    pass

    # 5. Clean system temp yfinance sqlite files (/tmp/*yfinance* /tmp/*.sqlite)
    tmp_dir = tempfile.gettempdir()
    if tmp_dir and os.path.exists(tmp_dir):
        try:
            for pattern in ["*yfinance*", "*stockcapture*", "*.sqlite", "*.sqlite3"]:
                for match in glob.glob(os.path.join(tmp_dir, pattern)):
                    try:
                        if os.path.isfile(match):
                            sz = os.path.getsize(match)
                            os.remove(match)
                            removed_files += 1
                            removed_bytes += sz
                        elif os.path.isdir(match):
                            shutil.rmtree(match, ignore_errors=True)
                            removed_dirs += 1
                    except Exception:
                        pass
        except Exception:
            pass

    if verbose:
        mb_freed = removed_bytes / (1024 * 1024)
        print(f"🧹 [CLEANUP] Startup disk & SQLite cleanup completed: {removed_files} files, {removed_dirs} cache dirs removed ({mb_freed:.2f} MB freed).")

    return {
        "files_removed": removed_files,
        "dirs_removed": removed_dirs,
        "bytes_freed": removed_bytes
    }

if __name__ == "__main__":
    cleanup_all_temp_data(verbose=True)
