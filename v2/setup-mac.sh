#!/bin/bash
# CardVault: one-command setup for a Mac with nothing installed.
#
#   curl -fsSL https://raw.githubusercontent.com/skazmi520/CardVault/main/v2/setup-mac.sh | bash
#
# What it does, in order:
#   1. finds Python 3.10 or newer, or installs Python from python.org
#      (macOS asks for your password once for that step)
#   2. downloads CardVault into ~/CardVault (no git needed)
#   3. gives it its own Python environment and installs what it needs
#   4. creates your empty database in ~/.cardvaultmac
#   5. puts "CardVault v2" in your Applications folder and opens it
#
# Run the same command again any time to update. Your cards, deals and
# settings live in ~/.cardvaultmac and are never touched by an update.
set -euo pipefail

# Everything runs inside main() so a piped download (curl | bash) is read in
# full before anything executes.
main() {
REPO_ZIP="${CARDVAULT_ZIP:-https://github.com/skazmi520/CardVault/archive/refs/heads/main.zip}"
DEST="${CARDVAULT_DIR:-$HOME/CardVault}"
PY_FALLBACK="3.13.16"

say()  { printf "\n\033[1m==> %s\033[0m\n" "$*"; }
fail() { printf "\n\033[31mSetup stopped: %s\033[0m\n" "$*"; exit 1; }

[ "$(uname)" = "Darwin" ] || fail "this installer is for macOS."

# ── 1. Python 3.10+ ────────────────────────────────────────────────────────────
py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }

find_python() {
  local c
  for c in /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
           /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    [ -x "$c" ] && py_ok "$c" && { echo "$c"; return 0; }
  done
  # /usr/bin/python3 is a stub on a fresh Mac that pops up an Xcode install
  # dialog, so only try it when the developer tools are really there.
  if xcode-select -p >/dev/null 2>&1 && py_ok /usr/bin/python3; then
    echo /usr/bin/python3; return 0
  fi
  return 1
}

PY="$(find_python || true)"
if [ -z "$PY" ]; then
  say "Installing Python from python.org (one time)"
  ver="$(curl -fsSL https://www.python.org/ftp/python/ | grep -o '3\.13\.[0-9]*/' | sort -V | tail -1 | tr -d / || true)"
  url="https://www.python.org/ftp/python/$ver/python-$ver-macos11.pkg"
  if [ -z "$ver" ] || ! curl -fsIL "$url" >/dev/null 2>&1; then
    ver="$PY_FALLBACK"; url="https://www.python.org/ftp/python/$ver/python-$ver-macos11.pkg"
  fi
  pkg="$(mktemp -d)/python.pkg"
  curl -fL --progress-bar "$url" -o "$pkg" || fail "could not download Python from $url"
  echo "macOS will ask for your Mac login password to install Python."
  sudo installer -pkg "$pkg" -target / </dev/tty || fail "Python install was cancelled."
  cert="/Applications/Python ${ver%.*}/Install Certificates.command"
  [ -f "$cert" ] && bash "$cert" >/dev/null 2>&1 || true
  PY="$(find_python || true)"
  [ -n "$PY" ] || fail "Python installed but could not be found. Restart Terminal and run this again."
fi
say "Using $("$PY" --version) at $PY"

# ── 2. Download CardVault ─────────────────────────────────────────────────────
say "Downloading CardVault into $DEST"
tmp="$(mktemp -d)"
if [ -f "$REPO_ZIP" ]; then cp "$REPO_ZIP" "$tmp/cv.zip"
else curl -fL --progress-bar "$REPO_ZIP" -o "$tmp/cv.zip" || fail "could not download CardVault."
fi
unzip -q "$tmp/cv.zip" -d "$tmp/src"
src="$(find "$tmp/src" -mindepth 1 -maxdepth 1 -type d | head -1)"
[ -f "$src/v2/app.py" ] || fail "the download did not look like CardVault."
mkdir -p "$DEST"
# Replace the code, keep this machine's Python environment.
rsync -a --delete --exclude ".venv" --exclude "CardVault v2.app" "$src/" "$DEST/"
rm -rf "$tmp"
chmod +x "$DEST"/v2/*.sh "$DEST/check" 2>/dev/null || true

# ── 3. Private Python environment ─────────────────────────────────────────────
say "Setting up CardVault's Python environment"
[ -x "$DEST/.venv/bin/python3" ] || "$PY" -m venv "$DEST/.venv"
# shellcheck disable=SC1091
source "$DEST/.venv/bin/activate"
python3 -m pip install --quiet --upgrade pip

# ── 4 and 5. Dependencies, database, app bundle ───────────────────────────────
cd "$DEST"
./v2/install.sh

mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/CardVault v2.app"
cp -R "$DEST/CardVault v2.app" "$HOME/Applications/"

say "Done. Opening CardVault"
echo "From now on, open it from Launchpad or Spotlight (search \"CardVault\")."
echo "It runs in your browser at http://127.0.0.1:5177"
[ -n "${CARDVAULT_NO_OPEN:-}" ] || open "$HOME/Applications/CardVault v2.app"
}

main "$@"
