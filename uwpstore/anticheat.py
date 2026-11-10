"""Proton's anti-cheat runtimes, when one is already on this machine.

Steam installs "Proton EasyAntiCheat Runtime" and "Proton BattlEye Runtime" as tools; Lutris and Heroic keep their
own copies. A game whose developer enabled EasyAntiCheat or BattlEye on Linux ships the Linux library beside the
Windows one, and Wine-UWP then loads the runtime's bridge in its place (as Proton does) when PROTON_EAC_RUNTIME or
PROTON_BATTLEYE_RUNTIME names the runtime. The store never downloads these; it only points at a copy it finds.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

HOME = Path.home()
STEAM_ROOTS = [HOME / ".local/share/Steam", HOME / ".steam/steam", HOME / ".steam/root",
               HOME / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]

# (environment variable, Steam tool folder, file that marks a usable copy, other launchers' copies)
RUNTIMES = [
    ("PROTON_EAC_RUNTIME", "Proton EasyAntiCheat Runtime", "v2/lib64/easyanticheat_x64.so",
     [HOME / ".local/share/lutris/runtime/eac_runtime", HOME / ".config/heroic/tools/runtimes/eac_runtime"]),
    ("PROTON_BATTLEYE_RUNTIME", "Proton BattlEye Runtime", "v1/lib64/beclient_x64.so",
     [HOME / ".local/share/lutris/runtime/battleye_runtime", HOME / ".config/heroic/tools/runtimes/battleye_runtime"]),
]


def _steam_libraries() -> list[Path]:
    found: list[Path] = []
    for root in STEAM_ROOTS:
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(errors="replace")
        except OSError:
            continue
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            path = Path(match.group(1).replace("\\\\", "\\"))
            if path not in found:
                found.append(path)
        if root not in found:
            found.append(root)
    return found


@lru_cache(maxsize=1)
def runtimes() -> dict[str, str]:
    """{environment variable: runtime folder} for each runtime found."""
    out: dict[str, str] = {}
    libraries = _steam_libraries()
    for variable, tool, marker, others in RUNTIMES:
        candidates = [lib / "steamapps" / "common" / tool for lib in libraries] + others
        for folder in candidates:
            if (folder / marker).is_file():
                out[variable] = str(folder)
                break
    return out


def env() -> dict[str, str]:
    """The variables to add to a game's environment (a value the user set already wins)."""
    return {k: v for k, v in runtimes().items() if not os.environ.get(k)}
