#!/bin/bash
# ===========================================
# Auto Dev Loop - Quick Start Script
# ===========================================

set -e

echo "============================================"
echo "  AUTO DEV LOOP - Starting"
echo "============================================"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required"
    exit 1
fi

# Check API Keys
echo ""
echo "Checking API Keys..."

if [ -z "$GLM_API_KEY" ]; then
    echo "⚠️  GLM_API_KEY not set"
    echo "   Get it from: https://open.bigmodel.cn/"
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "⚠️  ANTHROPIC_API_KEY not set"
    echo "   Get it from: https://console.anthropic.com/"
fi

if [ -z "$GOOGLE_API_KEY" ]; then
    echo "⚠️  GOOGLE_API_KEY not set (optional for Gemini)"
fi

# Install dependencies
echo ""
echo "Installing dependencies..."
pip3 install -q pyyaml requests playwright

# Install Playwright browsers
if ! command -v playwright &> /dev/null; then
    echo "Installing Playwright browsers..."
    playwright install chromium
fi

# Run
echo ""
echo "============================================"
echo "  Starting Auto Dev Loop"
echo "============================================"

cd "$(dirname "$0")"

python3 scripts/main_loop.py \
    --app "A beautiful weather dashboard with 7-day forecast" \
    --max-iterations 20 \
    --target-score 8.0
