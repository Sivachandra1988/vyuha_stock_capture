#!/usr/bin/env bash
# ==============================================================================
# Trader Pilot & UMX Vyuha - Integrated Web UI & Scanner Engine (Linux/Termux)
# ==============================================================================

PORT=${1:-8080}
HOST=${2:-0.0.0.0}

# ------------------------------------------------------------------------------
# Timezone Configuration (Default: Indian Standard Time / Asia/Kolkata)
# ------------------------------------------------------------------------------
export TZ="${TZ:-Asia/Kolkata}"

# Apply system timezone if running on Linux with permissions or container
if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-timezone "$TZ" 2>/dev/null || true
elif [ -f "/usr/share/zoneinfo/$TZ" ] && [ -w /etc/localtime ]; then
    ln -sf "/usr/share/zoneinfo/$TZ" /etc/localtime 2>/dev/null || true
    echo "$TZ" > /etc/timezone 2>/dev/null || true
fi

echo "======================================================================"
echo "  TRADER PILOT & UMX VYUHA - INTEGRATED SERVICE LAUNCHER"
echo "======================================================================"
echo "Binding to      : $HOST:$PORT"
echo "System Timezone : $TZ ($(date))"
echo ""

# Activate virtualenv if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Launch integrated dashboard and scanner
python3 dashboard_server.py --host "$HOST" --port "$PORT" --no-browser

