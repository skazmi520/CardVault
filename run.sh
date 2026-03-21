#!/usr/bin/env bash
# CardVault Mac — launcher script
# Run from the CardVaultMac directory: bash run.sh

set -e
cd "$(dirname "$0")"

PYTHON=python3

# Check Python 3
if ! command -v $PYTHON &>/dev/null; then
    echo "❌  python3 not found. Install it from https://python.org or via Homebrew: brew install python3"
    exit 1
fi

# Create venv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "⏳  Creating virtual environment..."
    $PYTHON -m venv .venv
fi

source .venv/bin/activate

# Install / upgrade dependencies quietly
echo "⏳  Checking dependencies..."
pip install -q -r requirements.txt

echo "✅  Launching CardVault..."
python main.py
