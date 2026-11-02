#!/usr/bin/env bash
# ==============================================================================
# UMX VYUHA — 1-Click Integrated Launcher (Web Dashboard + Breakout Scanner)
# ==============================================================================

PORT=${1:-8080}
HOST=${2:-0.0.0.0}

echo "======================================================================"
echo "  🚀 UMX VYUHA — ALL-IN-ONE (WEB DASHBOARD + BREAKOUT SCANNER)"
echo "======================================================================"
echo "Binding Web UI to: http://$HOST:$PORT"
echo ""

# Activate virtualenv if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Launch all-in-one integrated server (serves web UI and runs scanner)
python3 dashboard_server.py --host "$HOST" --port "$PORT" --no-browser

