#!/bin/bash
# ============================================================================
# Subtitle Translator - macOS/Linux Stop Script
# ============================================================================

# Self-repair: If this script is not executable, re-invoke via bash
if [[ ! -x "$0" ]]; then
    exec /bin/bash "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.server.pid"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo -e "${CYAN}Stopping Subtitle Translator...${NC}"
echo ""

# Try to read saved PID
if [ -f "$PID_FILE" ]; then
    SERVER_PID=$(cat "$PID_FILE")
    if ps -p "$SERVER_PID" > /dev/null 2>&1; then
        echo "Stopping server (PID: $SERVER_PID)..."
        kill "$SERVER_PID" 2>/dev/null
        rm -f "$PID_FILE"
    fi
fi

# Also kill any python processes running run.py
pkill -f llama-server 2>/dev/null
pkill -f "python.*run.py" 2>/dev/null
pkill -f "python3.*run.py" 2>/dev/null

echo ""
echo -e "${GREEN}Subtitle Translator has been stopped.${NC}"
echo ""
