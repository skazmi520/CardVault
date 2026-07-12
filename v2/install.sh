#!/bin/bash
# CardVault v2 — one-command install. No v1 required.
#
#   ./v2/install.sh
#
# - installs Python dependencies (flask, Pillow)
# - creates the v2 database:
#     * migrates your v1 data if a v1 database exists
#     * otherwise creates a fresh empty database
# - builds the dock-launchable "CardVault v2.app"
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null; then
  echo "python3 not found. Install it from https://www.python.org/downloads/ or 'brew install python'."
  exit 1
fi
PYV=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Using python3 $PYV ($(command -v python3))"

echo "Installing dependencies (flask, Pillow)…"
python3 -m pip install --quiet flask "Pillow>=10.0.0"

DB_V2="$HOME/.cardvaultmac/cardvault_v2.db"
DB_V1="$HOME/.cardvaultmac/cardvault.db"

if [ -f "$DB_V2" ]; then
  echo "v2 database already exists — keeping it."
elif [ -f "$DB_V1" ]; then
  echo "Found a v1 database — migrating your data into v2 (v1 is never modified)…"
  python3 -m v2.migrate_v1_to_v2
else
  echo "Fresh install — creating an empty v2 database…"
  python3 -m v2.init_db
fi

echo "Building the dock app…"
./v2/build_v2_app.sh

echo
echo "Done. Launch CardVault v2 either way:"
echo "  • double-click 'CardVault v2.app' (drag it to your Dock)"
echo "  • python3 -m v2.app        → http://127.0.0.1:5177"
