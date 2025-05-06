#!/usr/bin/env bash
# build-appimage.sh – verbose builder for Audio‑to‑Text‑Transcriber
set -euo pipefail

banner() { echo -e "\n\033[1;34m>>> $*\033[0m"; }
die()    { echo -e "\033[0;31m✗ $*\033[0m"; exit 1; }

APP=io.github.JaredTweed.AudioToTextTranscriber
VER=1.0.1
ZIP="v${VER}.zip"
APPDIR="${APP}.AppDir"

##############################################################################
# 1 – get the zip containing your already‑built release
##############################################################################
banner "[1/10] Check source zip"
if [[ -f $ZIP ]]; then
  echo "    ✓ $ZIP already present"
else
  wget -nv "https://github.com/JaredTweed/AudioToTextTranscriber/releases/download/v${VER}/${ZIP}" \
    || die "download failed for $ZIP"
fi

##############################################################################
# 2 – ensure linuxdeploy + its GTK and Python plugins are available
##############################################################################
banner "[2/10] Grab linuxdeploy + plugins"

fetch() {                    # fetch URL → file + make executable
  local url="$1" file="$2"
  [[ -f $file ]] && { echo "    ✓ $file ready"; return; }
  wget -nv "$url" -O "$file" || die "download failed for $file"
  chmod +x "$file"
}

# linuxdeploy core
fetch https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage \
      linuxdeploy-x86_64.AppImage
# GTK plugin (shell script, not an AppImage)
fetch https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/master/linuxdeploy-plugin-gtk.sh \
      linuxdeploy-plugin-gtk.sh
# Python plugin lives under niess/*
fetch https://github.com/niess/linuxdeploy-plugin-python/releases/download/continuous/linuxdeploy-plugin-python-x86_64.AppImage \
      linuxdeploy-plugin-python-x86_64.AppImage           # ← fixed URL

##############################################################################
# 3 – start a clean AppDir skeleton
##############################################################################
banner "[3/10] Re‑create $APPDIR"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"                                 # bin dir
mkdir -p "$APPDIR/usr/share/$APP"                          # <‑‑ NEW  ←←←
echo "    ✓ directory tree ready"

##############################################################################
# 4 – unpack your zip inside AppDir (now that the path exists)
##############################################################################
banner "[4/10] Unzip release"
unzip -q "$ZIP" -d "$APPDIR/usr/share/$APP" || die "unzip failed"
echo "    ✓ unzip OK"

##############################################################################
# 5 – copy desktop file, icon, metainfo
##############################################################################
banner "[5/10] Install desktop/icon/metainfo"
install -Dm644 "$APPDIR/usr/share/$APP/io.github.JaredTweed.AudioToTextTranscriber.desktop" "$APPDIR/usr/share/applications/$APP.desktop"
install -Dm644 "$APPDIR/usr/share/$APP/io.github.JaredTweed.AudioToTextTranscriber.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/$APP.png"
install -Dm644 "$APPDIR/usr/share/$APP/io.github.JaredTweed.AudioToTextTranscriber.metainfo.xml" "$APPDIR/usr/share/metainfo/$APP.metainfo.xml"
echo "    ✓ assets copied"

##############################################################################
# 6 – simple launcher wrapper
##############################################################################
banner "[6/10] Create launcher"
cat >"$APPDIR/usr/bin/audio-to-text-transcriber" <<'LAUNCH'
#!/usr/bin/env bash
exec /usr/bin/env python3 "$APPDIR/usr/share/io.github.JaredTweed.AudioToTextTranscriber/main.py" "$@"
LAUNCH
chmod +x "$APPDIR/usr/bin/audio-to-text-transcriber"
echo "    ✓ launcher written"

##############################################################################
# 7 – run linuxdeploy with GTK & Python plugins
##############################################################################
banner "[7/10] Run linuxdeploy (can take a minute …)"
export VERSION="$VER"
export DEPLOY_GTK_VERSION=3
export NO_STRIP=true
./linuxdeploy-x86_64.AppImage \
  --appdir "$APPDIR" \
  --desktop-file "$APPDIR/usr/share/applications/$APP.desktop" \
  --icon-file   "$APPDIR/usr/share/icons/hicolor/256x256/apps/$APP.png" \
  --plugin gtk \
  --plugin python \
  --output appimage \
  || die "linuxdeploy failed"


##############################################################################
# 8 – show the result
##############################################################################
banner "[8/10] Result"
ls -1 *.AppImage || die "No AppImage produced!"

banner "[9/10] Success — distribute the file above!"

