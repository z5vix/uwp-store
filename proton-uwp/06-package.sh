#!/usr/bin/env bash
# Package the built port in two forms:
#
#   1. $RUNTIME_OUT: a wine-uwp distribution tree (bin/wine-uwp, Wine, share/wine-uwp) with runtime.json marking it
#      flavour "proton-uwp" and pointing at GE's DXVK and vkd3d-proton. This is what the store launches games with.
#   2. --steam: also a Steam compatibility tool "$TOOL_NAME" next to GE's: a copy of the installed GE release with
#      the ported Wine laid over its files/, so Steam and umu-run can use it too.
#
# GE's release itself is never modified. Nothing of Microsoft's is packaged: the Windows components are harvested
# from the user's own Windows 11 ISO by the store's setup.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/env.sh"
[ -x "$BUILD/wine" ] || die "run 04-build.sh first"
steam=; [ "${1:-}" = --steam ] && steam=1

ge_release=
for d in "$HOME/.local/share/Steam/compatibilitytools.d" "$HOME/.steam/root/compatibilitytools.d"; do
  [ -f "$d/$GE_TAG/version" ] && { ge_release=$d/$GE_TAG; break; }
  for c in "$d/$GE_TAG"*; do [ -f "$c/version" ] && { ge_release=$c; break 2; }; done
done
[ -n "$ge_release" ] || echo "warning: $GE_TAG is not installed; games will use Wine's own D3D instead of DXVK/vkd3d-proton"

stage "Assembling the wine-uwp distribution"
tmp=$(mktemp -d "$(dirname "$RUNTIME_OUT" 2>/dev/null || echo "$WORK")/.pkg.XXXXXX" 2>/dev/null || mktemp -d)
mkdir -p "$(dirname "$RUNTIME_OUT")"
trap 'rm -rf "$tmp"' EXIT
WINE_UWP_ROOT=$UWP_SRC WINE_SRC=$PORT_SRC WINE_BUILD=$BUILD UWP_BIN=$WORK/uwp-bin WINEPREFIX=$WORK/build-prefix \
  "$UWP_SRC/packaging/build-dist.sh" "$tmp/dist"

d3d=
if [ -n "$ge_release" ]; then
  stage "Linking GE's DXVK and vkd3d-proton as the Direct3D runtime"
  d3d=$tmp/dist/lib/d3d-runtime/x86_64-windows
  mkdir -p "$d3d"
  for dll in dxgi d3d11 d3d10core d3d9 d3d8; do
    [ -f "$ge_release/files/lib/wine/dxvk/x86_64-windows/$dll.dll" ] && cp "$ge_release/files/lib/wine/dxvk/x86_64-windows/$dll.dll" "$d3d/"
  done
  for dll in d3d12 d3d12core; do
    [ -f "$ge_release/files/lib/wine/vkd3d-proton/x86_64-windows/$dll.dll" ] && cp "$ge_release/files/lib/wine/vkd3d-proton/x86_64-windows/$dll.dll" "$d3d/"
  done
  for dll in nvapi64 nvofapi64; do
    [ -f "$ge_release/files/lib/wine/nvapi/x86_64-windows/$dll.dll" ] && cp "$ge_release/files/lib/wine/nvapi/x86_64-windows/$dll.dll" "$d3d/"
  done
  d3d=$RUNTIME_OUT/lib/d3d-runtime/x86_64-windows
fi

wine_version=$("$BUILD/wine" --version 2>/dev/null || echo unknown)
cat > "$tmp/dist/runtime.json" <<EOF
{
  "id": "$(echo "$TOOL_NAME" | tr 'A-Z' 'a-z')",
  "name": "$TOOL_NAME (wine-uwp on GE-Proton)",
  "flavour": "proton-uwp",
  "wine_version": "$wine_version, wine-uwp $(git -C "$UWP_SRC" rev-parse --short HEAD), $GE_TAG",
  "d3d_runtime": "$d3d",
  "ge_release": "$ge_release",
  "env": {}
}
EOF
rm -rf "$RUNTIME_OUT"
mv "$tmp/dist" "$RUNTIME_OUT"
echo "READY runtime $RUNTIME_OUT"

if [ -n "$steam" ]; then
  [ -n "$ge_release" ] || die "--steam needs $GE_TAG installed"
  tool=$(dirname "$ge_release")/$TOOL_NAME
  stage "Steam compatibility tool $tool"
  rm -rf "$tool.tmp"
  cp -a "$ge_release" "$tool.tmp"
  # the ported Wine over GE's: binaries and every module it built; GE's extra modules (lsteamclient, steam.exe,
  # vrclient, DXVK, vkd3d-proton, nvapi) are left as they are
  cp -a "$RUNTIME_OUT/bin/wine" "$RUNTIME_OUT/bin/wineserver" "$tool.tmp/files/bin/"
  for arch in x86_64-unix x86_64-windows i386-windows; do
    [ -d "$RUNTIME_OUT/lib/wine/$arch" ] && cp -a "$RUNTIME_OUT/lib/wine/$arch/." "$tool.tmp/files/lib/wine/$arch/"
  done
  mkdir -p "$tool.tmp/files/share/wine-uwp"
  cp -a "$RUNTIME_OUT/share/wine-uwp/." "$tool.tmp/files/share/wine-uwp/"
  sed -i -E "s/\"$GE_TAG[^\"]*\"( *\/\/ Internal)/\"$TOOL_NAME\"\1/; s/\"display_name\" *\"[^\"]*\"/\"display_name\" \"$TOOL_NAME\"/" "$tool.tmp/compatibilitytool.vdf"
  echo "$(cut -d' ' -f1 "$ge_release/version") $TOOL_NAME" > "$tool.tmp/version"
  rm -rf "$tool"; mv "$tool.tmp" "$tool"
  echo "READY steam tool $tool"
fi
