#!/usr/bin/env bash
# Install UWP Store for this user: a uwp-store command (~/.local/bin), its icon, and a start-menu entry.
# Nothing outside your home directory is touched. Undo: ./install-desktop.sh --uninstall
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
id=io.github.uwpstore.Store
bin="$HOME/.local/bin/uwp-store"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -fv "$bin" "$apps/$id.desktop" "$icons/$id.svg"
else
  mkdir -p "$(dirname "$bin")" "$apps" "$icons"
  ln -sf "$here/uwp-store" "$bin"
  cp "$here/data/icons/$id.svg" "$icons/"
  # absolute Exec path: menus do not always have ~/.local/bin on PATH
  sed "s|^Exec=uwp-store|Exec=$bin|" "$here/data/$id.desktop" > "$apps/$id.desktop"
  echo "Installed: $bin, $apps/$id.desktop, $icons/$id.svg"
fi
command -v update-desktop-database >/dev/null && update-desktop-database -q "$apps" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$(dirname "$(dirname "$icons")")" 2>/dev/null || true
