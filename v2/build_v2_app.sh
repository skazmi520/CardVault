#!/bin/bash
# Build "CardVault v2.app" — a dock-launchable bundle that starts the v2
# server (if not already running) and opens it in the default browser.
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
APP="$PROJ/CardVault v2.app"
PYTHON="$(command -v python3)"

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

cat > "$APP/Contents/MacOS/launcher" <<EOF
#!/bin/bash
DIR="$PROJ"
URL="http://127.0.0.1:5177"
if ! curl -s -o /dev/null --max-time 1 "\$URL"; then
  cd "\$DIR"
  nohup "$PYTHON" -m v2.app >> "\$HOME/.cardvaultmac/v2_server.log" 2>&1 &
  for i in \$(seq 1 40); do
    curl -s -o /dev/null --max-time 1 "\$URL" && break
    sleep 0.3
  done
fi
open "\$URL"
EOF
chmod +x "$APP/Contents/MacOS/launcher"

[ -f "$PROJ/CardVault.icns" ] && cp "$PROJ/CardVault.icns" "$APP/Contents/Resources/icon.icns"

echo "Built: $APP"
echo "Drag it to your Dock (or Applications). It reuses a running server."
