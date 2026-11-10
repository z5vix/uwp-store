"""Start-menu entries for installed apps: one .desktop file per app, kept in step with what is installed.

Entries are named uwpstore-<runtime>-<stage>.desktop and carry X-UWPStore-App, so a sync can tell its own
entries from everything else and remove the ones whose app is gone. Icons are copied out of the package into the
store's data directory (the stage may move or be replaced by an update).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths, runtimes, settings

PREFIX = "uwpstore-"


def applications_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "applications"


def icons_dir() -> Path:
    return paths.data_dir() / "icons"


def launcher_command() -> list[str]:
    """How a menu entry starts the store's CLI: the installed command if there is one, else this checkout."""
    installed = Path.home() / ".local" / "bin" / "uwp-store"
    if installed.exists():
        return [str(installed)]
    script = Path(__file__).resolve().parents[1] / "uwp-store"
    return [str(script)] if script.exists() else [sys.executable, "-m", "uwpstore"]


def _quote(arg: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:+@=-]+", arg):
        return arg
    return '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'


def _entry_name(runtime: runtimes.Runtime, app: runtimes.InstalledApp) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{runtime.id}-{app.stage}")
    return f"{PREFIX}{safe}.desktop"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", " ").replace("\r", " ")


def write_entry(runtime: runtimes.Runtime, app: runtimes.InstalledApp, label: str = "") -> Path:
    target = applications_dir() / _entry_name(runtime, app)
    target.parent.mkdir(parents=True, exist_ok=True)
    icon = "io.github.uwpstore.Store"
    if app.icon_path and Path(app.icon_path).is_file():
        icons_dir().mkdir(parents=True, exist_ok=True)
        copy = icons_dir() / (target.stem + Path(app.icon_path).suffix.lower())
        shutil.copyfile(app.icon_path, copy)
        icon = str(copy)
    argv = launcher_command() + ["run", app.stage, "--runtime", runtime.id]
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={_escape(label or app.title or app.stage)}",
        f"Comment={_escape(f'Runs with {runtime.name}')}",
        f"Exec={' '.join(_quote(a) for a in argv)}",
        f"Icon={icon}",
        "Categories=Game;" if runtime.is_proton or "game" in app.title.lower() else "Categories=Utility;",
        "StartupNotify=false",
        f"X-UWPStore-App={runtime.id}/{app.stage}",
        "",
    ]
    text = "\n".join(lines)
    if not target.exists() or target.read_text(errors="replace") != text:
        target.write_text(text)
    return target


def remove_entry(runtime: runtimes.Runtime, app: runtimes.InstalledApp) -> None:
    target = applications_dir() / _entry_name(runtime, app)
    target.unlink(missing_ok=True)
    for icon in icons_dir().glob(target.stem + ".*"):
        icon.unlink(missing_ok=True)
    _refresh_menu()


def _ours(path: Path) -> bool:
    try:
        return "X-UWPStore-App=" in path.read_text(errors="replace")
    except OSError:
        return False


def sync() -> int:
    """Make the menu match what is installed (or remove every entry when the setting is off). Returns the count."""
    wanted: set[str] = set()
    if settings.load().get("app_shortcuts", True):
        listed = [(runtime, app) for runtime in runtimes.discover() for app in runtime.apps()
                  if app.launchable and app.in_app_list]
        titles = [app.title for _rt, app in listed]
        for runtime, app in listed:
            # the same app in two runtimes: say which one each entry starts
            label = f"{app.title} ({runtime.name})" if titles.count(app.title) > 1 else app.title
            wanted.add(write_entry(runtime, app, label).name)
    folder = applications_dir()
    for path in folder.glob(f"{PREFIX}*.desktop") if folder.is_dir() else []:
        if path.name not in wanted and _ours(path):
            path.unlink(missing_ok=True)
            for icon in icons_dir().glob(path.stem + ".*"):
                icon.unlink(missing_ok=True)
    _refresh_menu()
    return len(wanted)


def _refresh_menu() -> None:
    tool = shutil.which("update-desktop-database")
    if tool:
        subprocess.run([tool, "-q", str(applications_dir())], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
