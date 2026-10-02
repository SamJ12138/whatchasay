#!/bin/bash
# ============================================================================
# Subtitle Translator - macOS One-Click Launcher
# ============================================================================
# This script automatically handles permission issues from zip extraction.
# If double-clicking doesn't work, right-click → "Open With" → Terminal
# ============================================================================

# Self-repair: If this script is not executable, re-invoke via bash
if [[ ! -x "$0" ]]; then
    exec /bin/bash "$0" "$@"
fi

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
VENV_DIR="$BACKEND_DIR/venv"
PYTHON_EXE="$VENV_DIR/bin/python"
PIP_EXE="$VENV_DIR/bin/pip"
SERVER_HOST="127.0.0.1"
SERVER_PORT="8765"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Ensure scripts are executable for future runs
chmod +x "$SCRIPT_DIR/Start_Subtitle_Translator.command" 2>/dev/null
chmod +x "$SCRIPT_DIR/Stop_Subtitle_Translator.command" 2>/dev/null

# Print banner
echo ""
echo -e "${CYAN}============================================================================${NC}"
echo -e "${CYAN}           SUBTITLE TRANSLATOR - One-Click Launcher                         ${NC}"
echo -e "${CYAN}           Live captions + translation, local-first                      ${NC}"
echo -e "${CYAN}============================================================================${NC}"
echo ""

# ============================================================================
# Step 1: Check Python Installation
# ============================================================================
echo -e "${YELLOW}[1/6] Checking Python installation...${NC}"

# Check for python3
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}ERROR: Python 3 is not installed${NC}"
    echo ""
    echo "Please install Python 3.10+ using one of these methods:"
    echo "  - Homebrew: brew install python@3.11"
    echo "  - Download from https://python.org"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
echo "   Found Python $PYTHON_VERSION"

# Parse version
PY_MAJOR=$(echo "$PYTHON_VERSION" | cut -d'.' -f1)
PY_MINOR=$(echo "$PYTHON_VERSION" | cut -d'.' -f2)

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    echo -e "${RED}ERROR: Python 3.10+ is required. Found Python $PYTHON_VERSION${NC}"
    read -p "Press Enter to exit..."
    exit 1
fi

echo -e "   ${GREEN}OK${NC} - Python version is compatible"

# ============================================================================
# Step 2: Create/Verify Virtual Environment
# ============================================================================
echo ""
echo -e "${YELLOW}[2/6] Setting up Python virtual environment...${NC}"

if [ ! -d "$VENV_DIR" ]; then
    echo "   Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo -e "${RED}ERROR: Failed to create virtual environment${NC}"
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo -e "   ${GREEN}OK${NC} - Virtual environment created"
else
    echo -e "   ${GREEN}OK${NC} - Virtual environment exists"
fi

# Verify venv Python exists
if [ ! -f "$PYTHON_EXE" ]; then
    echo -e "${RED}ERROR: Virtual environment Python not found${NC}"
    echo "   Recreating virtual environment..."
    rm -rf "$VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# ============================================================================
# Step 3: Install Python Dependencies
# ============================================================================
echo ""
echo -e "${YELLOW}[3/6] Installing/verifying Python dependencies...${NC}"

# Check if dependencies need to be installed
"$PYTHON_EXE" -c "import fastapi, sherpa_onnx, ctranslate2" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "   Installing dependencies (this may take several minutes on first run)..."
    "$PIP_EXE" install --upgrade pip > /dev/null 2>&1
    "$PIP_EXE" install -r "$BACKEND_DIR/requirements.txt"
    if [ $? -ne 0 ]; then
        echo -e "${RED}ERROR: Failed to install dependencies${NC}"
        echo "   Try running: $PIP_EXE install -r $BACKEND_DIR/requirements.txt"
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo -e "   ${GREEN}OK${NC} - Dependencies installed"
else
    echo -e "   ${GREEN}OK${NC} - Dependencies already installed"
fi

# ============================================================================
# Step 4: Models
# ============================================================================
echo ""
echo -e "${YELLOW}[4/6] Models${NC}"
echo "   Speech models (English, Chinese, Bengali) and the language-ID model are"
echo "   downloaded automatically on first start (~350 MB)."
echo "   NOTE: HY-MT GPU translation needs NVIDIA CUDA (Windows/Linux). On macOS the"
echo "   backend uses OPUS-MT on CPU; add a Google/Azure key in Options for zh<->bn."
"$PYTHON_EXE" -c "import torch" 2>/dev/null || "$PIP_EXE" install torch >/dev/null 2>&1

# ============================================================================
# Step 6: Start the Backend Server
# ============================================================================
echo ""
echo -e "${YELLOW}[6/6] Starting backend server...${NC}"

# Check if server is already running
nc -z "$SERVER_HOST" "$SERVER_PORT" 2>/dev/null
if [ $? -eq 0 ]; then
    echo -e "   ${GREEN}Server is already running on http://$SERVER_HOST:$SERVER_PORT${NC}"
else
    echo "   Starting server on http://$SERVER_HOST:$SERVER_PORT..."
    cd "$BACKEND_DIR"

    # Start server in background
    "$PYTHON_EXE" run.py --host "$SERVER_HOST" --port "$SERVER_PORT" &
    SERVER_PID=$!

    # Save PID for stop script
    echo $SERVER_PID > "$SCRIPT_DIR/.server.pid"

    # Wait for server to start
    echo "   Waiting for server to start..."
    ATTEMPTS=0
    while [ $ATTEMPTS -lt 30 ]; do
        sleep 2
        ATTEMPTS=$((ATTEMPTS + 1))
        nc -z "$SERVER_HOST" "$SERVER_PORT" 2>/dev/null
        if [ $? -eq 0 ]; then
            break
        fi
    done

    nc -z "$SERVER_HOST" "$SERVER_PORT" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo -e "${RED}ERROR: Server failed to start within 60 seconds${NC}"
        echo "   Check the terminal for errors"
        read -p "Press Enter to exit..."
        exit 1
    fi

    echo -e "   ${GREEN}OK${NC} - Server started successfully"
fi

# ============================================================================
# Open Health Check in Browser
# ============================================================================
echo ""
echo -e "${GREEN}============================================================================${NC}"
echo -e "${GREEN}   Subtitle Translator is RUNNING!                                          ${NC}"
echo -e "${GREEN}============================================================================${NC}"
echo ""
echo "   Server:     http://$SERVER_HOST:$SERVER_PORT"
echo "   Debug UI:   http://$SERVER_HOST:$SERVER_PORT/debug"
echo "   Health:     http://$SERVER_HOST:$SERVER_PORT/health"
echo ""
echo "   Next Steps:"
echo "   1. Install the browser extension from the 'extension' folder"
echo "   2. Open a video with subtitles"
echo "   3. Enable translation from the extension popup"
echo ""

# Open browser
echo "   Opening health check page in browser..."
if command -v open &> /dev/null; then
    open "http://$SERVER_HOST:$SERVER_PORT/health"
elif command -v xdg-open &> /dev/null; then
    xdg-open "http://$SERVER_HOST:$SERVER_PORT/health"
fi

echo ""
echo -e "   ${CYAN}To stop the server, run: ./Stop_Subtitle_Translator.command${NC}"
echo -e "   ${CYAN}Or press Ctrl+C in this terminal${NC}"
echo ""

# Wait for user to close or Ctrl+C
if [ -n "$SERVER_PID" ]; then
    wait $SERVER_PID
fi
