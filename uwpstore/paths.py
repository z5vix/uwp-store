"""Where everything lives. All user data stays under XDG directories; nothing is written next to the code."""

from __future__ import annotations

import os
from pathlib import Path

APP_ID = "io.github.uwpstore.Store"
NAME = "uwp-store"


def _xdg(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable)
    return Path(value) if value else Path.home() / fallback


def data_dir() -> Path:
    path = Path(os.environ.get("UWP_STORE_DATA") or _xdg("XDG_DATA_HOME", ".local/share") / NAME)
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = _xdg("XDG_CACHE_HOME", ".cache") / NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    folder = _xdg("XDG_CONFIG_HOME", ".config") / NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "settings.json"


def downloads_dir() -> Path:
    path = data_dir() / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtimes_dir() -> Path:
    """Runtimes this app builds or adopts (wine-uwp trees, the GE-Proton UWP tool)."""
    path = data_dir() / "runtimes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def windows_dir() -> Path:
    """Windows components harvested from the user's own ISO. Shared by every runtime, never shipped."""
    return data_dir() / "windows"


def prefix_root() -> Path:
    path = data_dir() / "prefixes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def steam_compat_dirs() -> list[Path]:
    home = Path.home()
    candidates = [
        home / ".local/share/Steam/compatibilitytools.d",
        home / ".steam/root/compatibilitytools.d",
        home / ".var/app/com.valvesoftware.Steam/data/Steam/compatibilitytools.d",
    ]
    seen, out = set(), []
    for path in candidates:
        try:
            real = path.resolve()
        except OSError:
            continue
        if path.is_dir() and real not in seen:
            seen.add(real)
            out.append(path)
    return out
