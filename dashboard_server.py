import os
import sys
import time
import warnings

# Suppress 3rd-party library deprecation and future warnings (e.g. Pandas4Warning / yfinance inplace warnings)
warnings.filterwarnings("ignore")
os.environ.setdefault("PYTHONWARNINGS", "ignore")

# Set Indian Standard Time (IST) on POSIX/Linux platforms
if hasattr(time, "tzset"):
    os.environ.setdefault("TZ", "Asia/Kolkata")
    time.tzset()

# Force UTF-8 encoding
if hasattr(sys.stdout, "reconfigure") and sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure") and sys.stderr.encoding != "utf-8":
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add src/ to Python path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(BASE_DIR, "src"))

from dashboard import server

# Cleanup temporary disk and SQLite caches on startup
try:
    import cleanup
    cleanup.cleanup_all_temp_data(verbose=True)
except Exception as e:
    print(f"[WARN] Startup cleanup: {e}")

if __name__ == "__main__":
    try:
        server.run_server()
    except KeyboardInterrupt:
        pass
