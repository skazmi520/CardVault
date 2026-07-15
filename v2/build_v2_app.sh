#!/bin/bash
# Build "CardVault v2.app" — a dock-launchable bundle that starts the v2
# server (if not already running) and opens it in the default browser.
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
APP="$PROJ/CardVault v2.app"
PYTHON_HINT="$(command -v python3)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>CardVault v2</string>
  <key>CFBundleDisplayName</key><string>CardVault v2</string>
  <key>CFBundleIdentifier</key><string>com.cardvault.v2</string>
  <key>CFBundleVersion</key><string>2.0.0</string>
  <key>CFBundleShortVersionString</key><string>2.0.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>icon.icns</string>
  <key>LSUIElement</key><false/>
</dict></plist>
EOF

# The launcher does NOT trust a single absolute python3 path baked at build
# time — GUI-launched processes get a different environment than a shell,
# and conda/Homebrew pythons can shift under the hood between builds. It
# probes candidates at LAUNCH time and picks the first one that actually
# has Flask importable, so a moved/updated interpreter can't strand it.
cat > "$APP/Contents/MacOS/launcher" <<EOF
#!/bin/bash
DIR="$PROJ"
URL="http://127.0.0.1:5177"
LOG="\$HOME/.cardvaultmac/v2_server.log"
LOCK="\$HOME/.cardvaultmac/v2_launcher.lock"
mkdir -p "\$HOME/.cardvaultmac"

log() { echo "[\$(date '+%H:%M:%S')] \$*" >> "\$LOG"; }

# Already up? Just open the browser.
if curl -s -o /dev/null --max-time 1 "\$URL"; then
  open "\$URL"
  exit 0
fi

# Prevent a double-click race from spawning two servers that fight over
# the port (a symptom that can masquerade as a random-looking failure).
if ! mkdir "\$LOCK" 2>/dev/null; then
  log "another launch already in progress — waiting"
  for i in \$(seq 1 40); do
    curl -s -o /dev/null --max-time 1 "\$URL" && open "\$URL" && exit 0
    sleep 0.3
  done
  open "\$URL"
  exit 0
fi
trap 'rmdir "\$LOCK" 2>/dev/null' EXIT

log "=== launch attempt ==="

# Find a python3 that actually has our dependencies, trying the interpreter
# active at build time first, then whatever a normal shell would resolve,
# then the common install locations directly.
CANDIDATES=(
  "$PYTHON_HINT"
  "python3"
  "/usr/local/bin/python3"
  "/opt/homebrew/bin/python3"
  "/usr/bin/python3"
  "/opt/homebrew/Caskroom/miniconda/base/bin/python3"
  "\$HOME/miniconda3/bin/python3"
)
PY=""
for c in "\${CANDIDATES[@]}"; do
  resolved="\$(command -v "\$c" 2>/dev/null || true)"
  [ -z "\$resolved" ] && continue
  if "\$resolved" -c "import flask, PIL" >/dev/null 2>&1; then
    PY="\$resolved"
    break
  fi
done

if [ -z "\$PY" ]; then
  log "no python3 with flask+Pillow found among: \${CANDIDATES[*]}"
  osascript -e 'display dialog "CardVault v2 could not find a Python install with its dependencies (flask, Pillow).\n\nRun ./v2/install.sh from Terminal in the CardVaultMac folder, then try again." buttons {"OK"} default button 1 with title "CardVault v2"' >/dev/null 2>&1
  exit 1
fi
log "using python3: \$PY"

# Don't depend on cd (Documents may be TCC-blocked for app bundles):
# PYTHONPATH makes the v2 package importable regardless of cwd.
export PYTHONPATH="\$DIR"
cd "\$DIR" 2>>"\$LOG" || log "cd failed (Documents access?) — continuing via PYTHONPATH"

nohup "\$PY" -m v2.app >> "\$LOG" 2>&1 &
SERVER_PID=\$!

ok=""
for i in \$(seq 1 40); do
  curl -s -o /dev/null --max-time 1 "\$URL" && ok=1 && break
  kill -0 "\$SERVER_PID" 2>/dev/null || break   # server process died — stop waiting
  sleep 0.3
done

if [ -z "\$ok" ]; then
  detail="\$(tail -n 12 "\$LOG" 2>/dev/null | sed 's/"/\\\\"/g')"
  log "server failed to come up"
  osascript -e "display dialog \"CardVault v2 failed to start.\n\nLast log lines:\n\$detail\" buttons {\"OK\"} default button 1 with title \"CardVault v2\""  >/dev/null 2>&1
  exit 1
fi
open "\$URL"
EOF
chmod +x "$APP/Contents/MacOS/launcher"

[ -f "$PROJ/CardVault.icns" ] && cp "$PROJ/CardVault.icns" "$APP/Contents/Resources/icon.icns"

echo "Built: $APP"
echo "Drag it to your Dock (or Applications). It reuses a running server."
