#!/usr/bin/env bash
# ==============================================================================
# Trader Pilot & UMX Vyuha - Integrated Web UI & Scanner Engine (Linux/Termux)
# ==============================================================================

PORT=${1:-8080}
HOST=${2:-0.0.0.0}

echo "======================================================================"
echo "  TRADER PILOT & UMX VYUHA - INTEGRATED SERVICE LAUNCHER"
echo "======================================================================"
echo "Binding to: $HOST:$PORT"
echo ""

# Activate virtualenv if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Launch integrated dashboard and scanner
python3 dashboard_server.py --host "$HOST" --port "$PORT" --no-browser

