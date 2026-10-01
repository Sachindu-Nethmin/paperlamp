#!/bin/bash
# Build PaperLamp.app and install it.
#
#   packaging/build_app.sh                 # installs to ~/Applications
#   packaging/build_app.sh /Applications   # or anywhere else
#   LINK_JOBS=1 packaging/build_app.sh     # also show this checkout's jobs/ in the app
#                                          # (macOS will ask once for access to that folder)
#
# Needs: Xcode command-line tools (swiftc), python3 with numpy and pillow,
# Homebrew poppler and ffmpeg, and the Ollama app for writing scripts.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/Applications}"
PY="$(command -v python3)"
BUILD="$HERE/build"
APP="$BUILD/PaperLamp.app"
SUPPORT="$HOME/Library/Application Support/PaperLamp"

echo "Checking requirements"
"$PY" -c "import numpy, PIL" 2>/dev/null || { echo "  python3 needs numpy and pillow: $PY -m pip install -r requirements.txt"; exit 1; }
for tool in pdftotext pdftoppm pdftohtml ffmpeg; do
  command -v "$tool" >/dev/null || echo "  warning: $tool not found (brew install poppler ffmpeg)"
done
[ -d /Applications/Ollama.app ] || echo "  note: Ollama.app not found; it is needed to write scripts (https://ollama.com)"

echo "Building the app"
rm -rf "$APP" "$BUILD/AppIcon.iconset"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"
# target macOS 12+ explicitly (the compiler's default can be newer than the running system)
swiftc -O -target "$(uname -m)-apple-macos12.0" -o "$APP/Contents/MacOS/PaperLamp" \
  "$HERE/packaging/macos/PaperLamp.swift" -framework Cocoa -framework WebKit
VERSION="$(cd "$HERE" && git describe --tags --always 2>/dev/null || echo 1.0)"
BUILDNO="$(cd "$HERE" && git rev-list --count HEAD 2>/dev/null || echo 1)"
sed -e "s|@PYTHON@|$PY|" -e "s|@VERSION@|1.0 ($VERSION)|" -e "s|@BUILD@|$BUILDNO|" \
  "$HERE/packaging/macos/Info.plist" > "$APP/Contents/Info.plist"
"$PY" "$HERE/packaging/macos/make_icon.py" "$BUILD/AppIcon.iconset"
iconutil -c icns "$BUILD/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"
rsync -a --exclude "__pycache__" "$HERE/app.py" "$HERE/paperlamp" "$HERE/ui" "$HERE/LICENSE" "$HERE/README.md" \
  "$APP/Contents/Resources/app/"
xattr -cr "$APP"                                   # Finder metadata from the Desktop blocks signing
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || echo "  note: ad-hoc signing skipped"

echo "Setting up app data in $SUPPORT"
mkdir -p "$SUPPORT"
# settings (voice paths etc.); videos are kept in the app's own folder, which needs no
# privacy permission (a link into Desktop or Documents makes macOS ask, and wait, first)
if [ ! -e "$SUPPORT/config.json" ] && [ -f "$HERE/config.json" ]; then cp "$HERE/config.json" "$SUPPORT/config.json"; fi
if [ "${LINK_JOBS:-0}" = "1" ] && [ ! -e "$SUPPORT/jobs" ] && [ -d "$HERE/jobs" ]; then ln -s "$HERE/jobs" "$SUPPORT/jobs"; fi

echo "Installing to $DEST"
mkdir -p "$DEST"
rm -rf "$DEST/PaperLamp.app"
ditto "$APP" "$DEST/PaperLamp.app"            # keeps the signature intact
echo "Done: $DEST/PaperLamp.app"
