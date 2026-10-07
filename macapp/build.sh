#!/bin/bash
# Builds Lupa.app and installs it into /Applications.
set -e
cd "$(dirname "$0")"
PROJECT="$(cd .. && pwd)"
# Build outside the project so Spotlight/Launchpad don't list a second copy of the app.
BUILD="$(mktemp -d)"
APP="$BUILD/Lupa.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O main.swift -o "$APP/Contents/MacOS/Lupa" -framework Cocoa -framework WebKit -framework Carbon
cp Lupa.icns "$APP/Contents/Resources/Lupa.icns"
cat > "$APP/Contents/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Lupa</string>
  <key>CFBundleDisplayName</key><string>Lupa</string>
  <key>CFBundleIdentifier</key><string>pl.smartpixel.lupa</string>
  <key>LupaProjectPath</key><string>$PROJECT</string>
  <key>CFBundleExecutable</key><string>Lupa</string>
  <key>CFBundleIconFile</key><string>Lupa</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>© Smart Pixel · MIT · EmbeddingGemma 2</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PL
codesign --force --deep -s - "$APP"
rm -rf /Applications/Lupa.app && cp -R "$APP" /Applications/Lupa.app
touch /Applications/Lupa.app
rm -rf "$BUILD"
echo "✅ /Applications/Lupa.app"
