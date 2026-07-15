#!/bin/bash

# Yanyu-Wit Startup Script for macOS/Linux
# Colors for fancy terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== 🚀 Yanyu-Wit Backend Service Starter ===${NC}\n"

# Parse arguments
DEPLOY_MODE="${WIT_DEPLOY_MODE:-pc}"
PORT=""

show_usage() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  -m, --mode     Set deploy profile: pc, saas (default: pc)"
    echo "  -s, --saas     Shorthand for --mode saas (backward compatible)"
    echo "  -p, --port     Override listening port (reads WIT_PORT from env/dotenv by default)"
    echo "  -h, --help     Show this help message"
    echo ""
    echo "Available profiles:"
    echo "  pc          - SQLite + Local sandbox + OIDC PKCE + Heartbeat"
    echo "  saas        - PostgreSQL + Docker sandbox + OIDC Confidential + No Heartbeat"
    echo ""
    exit 0
}

while [[ "$#" -gt 0 ]]; do
    case $1 in
        -m|--mode) DEPLOY_MODE="$2"; shift 2 ;;
        -s|--saas) DEPLOY_MODE="saas"; shift ;;
        -p|--port) PORT="$2"; shift 2 ;;
        -h|--help) show_usage ;;
        *) echo -e "${RED}Unknown option: $1${NC}"; show_usage ;;
    esac
done

# Ensure we are in the project root directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR" || exit 1

# Check if uv is installed
if command -v uv &> /dev/null; then
    echo -e "${GREEN}[INFO] Found 'uv' package manager. Using uv environment.${NC}"
    RUN_CMD="uv run python -m service.app"
    
    # Check if virtual environment is initialized
    if [ ! -d ".venv" ]; then
        echo -e "${YELLOW}[WARN] Virtual environment '.venv' not found. Syncing packages with uv sync...${NC}"
        uv sync
    fi
else
    # Fallback to standard Python virtualenv if uv is not installed
    echo -e "${YELLOW}[WARN] 'uv' is not installed. Falling back to python3 virtual environment.${NC}"
    if [ -d ".venv" ]; then
        echo -e "${GREEN}[INFO] Activating existing '.venv'...${NC}"
        source .venv/bin/activate
        RUN_CMD="python -m service.app"
    else
        echo -e "${YELLOW}[WARN] '.venv' not found. Please initialize a virtual environment first, or install 'uv'.${NC}"
        RUN_CMD="python3 -m service.app"
    fi
fi

# Set deployment mode
export WIT_DEPLOY_MODE="$DEPLOY_MODE"
echo -e "${GREEN}[INFO] Deployment mode set to: ${YELLOW}${WIT_DEPLOY_MODE}${NC}"

# Set port
if [ -n "$PORT" ]; then
    export WIT_PORT="$PORT"
    echo -e "${GREEN}[INFO] Port override set to: ${YELLOW}${WIT_PORT}${NC}"
else
    export WIT_PORT="7025"
    echo -e "${GREEN}[INFO] Port set to default: ${YELLOW}${WIT_PORT}${NC}"
fi

# Run the backend
echo -e "${BLUE}[START] Starting Yanyu-Wit backend service...${NC}\n"
exec $RUN_CMD
