#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== McAllen Trading Analyst ==="

# Load .env if ANTHROPIC_API_KEY not already set
if [ -z "$ANTHROPIC_API_KEY" ] && [ -f "backend/.env" ]; then
  export $(grep -v '^#' backend/.env | xargs)
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "Error: ANTHROPIC_API_KEY is not set."
  echo "Add it to backend/.env or run: export ANTHROPIC_API_KEY=sk-ant-..."
  exit 1
fi

# Set up Python venv if needed
if [ ! -d "backend/.venv" ]; then
  echo "Setting up Python environment..."
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
  echo "Dependencies installed."
fi

# Kill any existing instance on port 8000
lsof -ti:8000 | xargs kill -9 2>/dev/null || true

# Start backend
echo "Starting backend on http://localhost:8000 ..."
cd "$SCRIPT_DIR/backend"
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Start watchlist agent
echo "Starting watchlist agent..."
.venv/bin/python watchlist_agent.py &
AGENT_PID=$!

# Start market scanner
echo "Starting market scanner..."
.venv/bin/python market_scanner.py &
SCANNER_PID=$!
cd "$SCRIPT_DIR"

sleep 2

# Open the app through the backend (not file://)
echo "Opening http://localhost:8000 ..."
if command -v open &>/dev/null; then
  open "http://localhost:8000"
elif command -v xdg-open &>/dev/null; then
  xdg-open "http://localhost:8000"
fi

echo ""
echo "Ready!"
echo "  App:             http://localhost:8000"
echo "  Watchlist agent: running in background"
echo "  Market scanner:  running in background"
echo ""
echo "Press Ctrl+C to stop."

trap "kill $BACKEND_PID $AGENT_PID $SCANNER_PID 2>/dev/null; echo 'Stopped.'" EXIT
wait $BACKEND_PID
