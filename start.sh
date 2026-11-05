#!/usr/bin/env bash
# ==============================================================================
# UMX VYUHA — 1-Click Integrated Launcher (Web Dashboard + Breakout Scanner)
# ==============================================================================

PORT=${1:-8080}
HOST=${2:-0.0.0.0}

# ------------------------------------------------------------------------------
# Timezone Configuration (Default: Indian Standard Time / Asia/Kolkata)
# ------------------------------------------------------------------------------
export TZ="${TZ:-Asia/Kolkata}"
# Suppress 3rd-party library deprecation warnings (e.g. Pandas4Warning / yfinance inplace warnings)
export PYTHONWARNINGS="ignore"

# Apply system timezone if running on Linux with permissions or container
if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-timezone "$TZ" 2>/dev/null || true
elif [ -f "/usr/share/zoneinfo/$TZ" ] && [ -w /etc/localtime ]; then
    ln -sf "/usr/share/zoneinfo/$TZ" /etc/localtime 2>/dev/null || true
    echo "$TZ" > /etc/timezone 2>/dev/null || true
fi

echo "======================================================================"
echo "  🚀 UMX VYUHA — ALL-IN-ONE (WEB DASHBOARD + BREAKOUT SCANNER)"
echo "======================================================================"
echo "Binding Web UI to : http://$HOST:$PORT"
echo "System Timezone   : $TZ ($(date))"
echo ""

# ------------------------------------------------------------------------------
# Pre-Startup Cleanup: Remove Disk Temp Data & SQLite Caches
# ------------------------------------------------------------------------------
echo "🧹 Cleaning temporary files and SQLite caches from disk..."
rm -rf ~/.cache/py-yfinance/* ~/.cache/yfinance/* /tmp/py-yfinance* /tmp/*.sqlite* 2>/dev/null || true
rm -rf data/backtest_cache/* 2>/dev/null || true
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f \( -name "*.pyc" -o -name "*.pyo" -o -name "*.tmp" -o -name "*.temp" -o -name "*.sqlite*" -o -name "*.db-wal" -o -name "*.db-shm" \) -delete 2>/dev/null || true
echo "✓ Disk & SQLite temp cache cleared."
echo ""

# Activate virtualenv if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Launch all-in-one integrated server (serves web UI and runs scanner)
python3 dashboard_server.py --host "$HOST" --port "$PORT" --no-browser

