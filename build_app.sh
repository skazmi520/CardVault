#!/usr/bin/env bash
# Build CardVault.app and install it to /Applications
# Run from the CardVaultMac directory: bash build_app.sh
set -e
cd "$(dirname "$0")"

APP_NAME="CardVault"
APP_BUNDLE="${APP_NAME}.app"
SOURCE_DIR="$(pwd)"
VENV_DIR="${SOURCE_DIR}/.venv"

echo "⏳  Checking dependencies..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install -q -r requirements.txt

echo "⏳  Generating icon..."
python3 create_icon.py

echo "⏳  Building ${APP_BUNDLE}..."

# Remove old bundle
rm -rf "${APP_BUNDLE}"

# Create structure
mkdir -p "${APP_BUNDLE}/Contents/MacOS"
mkdir -p "${APP_BUNDLE}/Contents/Resources"

# Copy icon
cp CardVault.icns "${APP_BUNDLE}/Contents/Resources/CardVault.icns"

# Write Info.plist
cat > "${APP_BUNDLE}/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key>      <string>CardVault</string>
    <key>CFBundleExecutable</key>       <string>CardVault</string>
    <key>CFBundleIconFile</key>         <string>CardVault</string>
    <key>CFBundleIdentifier</key>       <string>com.cardvault.mac</string>
    <key>CFBundleInfoDictionaryVersion</key> <string>6.0</string>
    <key>CFBundleName</key>             <string>CardVault</string>
    <key>CFBundlePackageType</key>      <string>APPL</string>
    <key>CFBundleShortVersionString</key> <string>1.1</string>
    <key>CFBundleVersion</key>          <string>1.1.4</string>
    <key>LSMinimumSystemVersion</key>   <string>12.0</string>
    <key>NSHighResolutionCapable</key>  <true/>
    <key>NSRequiresAquaSystemAppearance</key> <false/>
    <key>LSUIElement</key>              <false/>
</dict>
</plist>
PLIST

# Write launcher executable
# Use the venv Python directly — avoids PATH issues when launched from Finder/Launchpad
PYTHON_BIN="${VENV_DIR}/bin/python3"
LOG_FILE="\$HOME/Library/Logs/CardVault.log"
cat > "${APP_BUNDLE}/Contents/MacOS/${APP_NAME}" << LAUNCHER
#!/usr/bin/env bash
cd "${SOURCE_DIR}"
exec "${PYTHON_BIN}" "${SOURCE_DIR}/main.py" 2>>"${LOG_FILE}"
LAUNCHER

chmod +x "${APP_BUNDLE}/Contents/MacOS/${APP_NAME}"

echo "✅  Built ${APP_BUNDLE}"

# Install to /Applications
echo "⏳  Installing to /Applications..."
rm -rf "/Applications/${APP_BUNDLE}"
cp -r "${APP_BUNDLE}" "/Applications/${APP_BUNDLE}"

# Refresh Finder so the icon appears immediately
touch "/Applications/${APP_BUNDLE}"
killall Finder 2>/dev/null || true

echo ""
echo "✅  CardVault is installed in /Applications"
echo "    You can now launch it from Spotlight, Launchpad, or the Applications folder."
echo ""
echo "    Note: On first launch macOS may say the app is from an unidentified developer."
echo "    Right-click the app → Open → Open to bypass this once."
