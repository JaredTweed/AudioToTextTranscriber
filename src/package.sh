#!/usr/bin/env bash
set -euo pipefail

PKG_DIR="$(pwd)"
OUT_ZIP="v1.0.1.zip"
SCRIPT_NAME="$(basename "$0")"

# 1) Remove any lines containing "stdc++fs" from all CMakeLists.txt files
find "$PKG_DIR" -name CMakeLists.txt -exec sed -i '/stdc++fs/d' {} +

# 2) Prepare a staging area with strip-components=1 semantics
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

cd "$PKG_DIR"

# Loop over every item under . (except the zip and script itself)
find . -mindepth 1 \( -path "./$OUT_ZIP" -o -path "./$SCRIPT_NAME" \) -prune -o -print | while read -r SRC; do
  # remove the leading “./”
  P="${SRC#./}"
  # drop the first path component
  #  • if there is no slash, this yields the basename
  #  • if there is, it yields everything after the first slash
  STRIPPED="${P#*/}"

  # Determine destination in staging
  DST="$TMPDIR/$STRIPPED"

  if [ -d "$SRC" ]; then
    mkdir -p "$DST"
  else
    mkdir -p "$(dirname "$DST")"
    cp -a "$SRC" "$DST"
  fi
done

# 3) Zip up the staging area (preserving the new structure)
(
  cd "$TMPDIR"
  zip -r "$PKG_DIR/$OUT_ZIP" . 
)

echo "Created $OUT_ZIP with:"
zipinfo -1 "$OUT_ZIP" | sed 's/^/  • /'


