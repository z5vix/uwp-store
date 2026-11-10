"""The Direct3D 12 layer GDK games need: DXVK's dxgi/d3d11 and vkd3d-proton's d3d12/d3d12core (x64).

Wine's own d3d12 lacks features these games test for at start, and vkd3d-proton presents through DXVK's dxgi.
Wine-UWP links the four DLLs next to every game executable that imports d3d12.dll when it installs the game,
from ``$WINE_UWP_D3D_RUNTIME`` (packaging/install-d3d-runtime.sh). The store keeps one shared copy, taken from a
Proton build already on the machine (GE-Proton, Proton-CachyOS, Proton Experimental...), and gives every
Wine-UWP runtime that has none of its own that copy.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import paths

DLLS = {"dxvk": ("dxgi.dll", "d3d11.dll"), "vkd3d-proton": ("d3d12.dll", "d3d12core.dll")}


def root() -> Path:
    return paths.data_dir() / "d3d-runtime" / "x86_64-windows"


def available() -> bool:
    return all((root() / dll).is_file() for dlls in DLLS.values() for dll in dlls)


def source() -> str:
    try:
        return (root().parent / "SOURCE").read_text().strip()
    except OSError:
        return ""


def _proton_trees() -> list[Path]:
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    folders = [home / ".steam" / "root" / "compatibilitytools.d", data / "Steam" / "compatibilitytools.d",
               home / ".steam" / "steam" / "steamapps" / "common", data / "Steam" / "steamapps" / "common",
               Path("/usr/share/steam/compatibilitytools.d")]
    seen: set[Path] = set()
    trees: list[Path] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for tree in folder.iterdir():
            try:
                real = tree.resolve()
            except OSError:
                continue
            if real in seen or not (real / "files" / "lib" / "wine").is_dir():
                continue
            seen.add(real)
            trees.append(real)

    def version(tree: Path) -> tuple:
        return tuple(int(n) for n in re.findall(r"\d+", tree.name)) or (0,)
    # the newest GE build first, then the others
    trees.sort(key=lambda t: ("GE-Proton" in t.name, version(t)), reverse=True)
    return trees


def find() -> Path | None:
    """A Proton tree with all four DLLs."""
    for tree in _proton_trees():
        wine = tree / "files" / "lib" / "wine"
        if all((wine / layer / "x86_64-windows" / dll).is_file() for layer, dlls in DLLS.items() for dll in dlls):
            return tree
    return None


def provide() -> Path | None:
    """Copy the layer from the newest local Proton into the store's data once; the folder, or None."""
    if available():
        return root()
    tree = find()
    if tree is None:
        return None
    target = root()
    target.mkdir(parents=True, exist_ok=True)
    wine = tree / "files" / "lib" / "wine"
    for layer, dlls in DLLS.items():
        for dll in dlls:
            shutil.copy2(wine / layer / "x86_64-windows" / dll, target / dll)
    (target.parent / "SOURCE").write_text(f"{tree}\n")
    return target
