"""Microsoft's WebView2 runtime, taken from the user's own Windows 11 ISO (never shipped or downloaded).

The Xbox app (and the Store purchase dialog inside it) draws parts of its UI with WebView2. Without a runtime in the
prefix, React Native's WebView handler dereferences the browser object it never got and XboxPcApp.exe crashes.
Wine-UWP installs the runtime into a prefix (with its own webview2host in front of it) from
``$WINE_UWP_DATA/webview2/rt/Program Files (x86)/Microsoft/EdgeWebView``, or from ``$WEBVIEW2_RUNTIME``; the store
extracts that folder once from the ISO's install.wim and points every runtime at the shared copy.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Iterator

from . import paths

_INNER = Path("Program Files (x86)") / "Microsoft" / "EdgeWebView"


def root() -> Path:
    return paths.data_dir() / "webview2" / "rt" / _INNER


def available() -> bool:
    return (root() / "Application").is_dir()


def version() -> str:
    folder = root() / "Application"
    if not folder.is_dir():
        return ""
    versions = sorted((p.name for p in folder.iterdir() if p.is_dir() and p.name[:1].isdigit()),
                      key=lambda v: tuple(int(x) for x in v.split(".") if x.isdigit()))
    return versions[-1] if versions else ""


def _seven_zip() -> str:
    for name in ("7z", "7zz", "7za"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("Extracting WebView2 needs 7-Zip (the 7z command; package p7zip or 7zip)")


def _run(argv: list[str], cancelled: Callable[[], bool]) -> None:
    process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    while process.poll() is None:
        if cancelled():
            process.kill()
            raise RuntimeError("Cancelled")
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
    if process.returncode not in (0, 1):            # 7z: 1 is a warning
        raise RuntimeError((process.stderr.read() if process.stderr else "").strip()[-300:] or "7z failed")


def extract(iso: Path, cancelled: Callable[[], bool] = lambda: False) -> Iterator[str]:
    """Copy install.wim out of the ISO (7z cannot read a WIM inside an ISO in place), extract EdgeWebView from
    its first image, and delete the copy. Yields STAGE lines like the runtime's own tools."""
    seven = _seven_zip()
    scratch_parent = paths.cache_dir()
    scratch_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="webview2-", dir=scratch_parent) as scratch:
        work = Path(scratch)
        yield "STAGE Copying install.wim out of the ISO (several GB)"
        _run([seven, "e", "-y", f"-o{work}", str(iso), "sources/install.wim"], cancelled)
        wim = work / "install.wim"
        if not wim.is_file():
            raise RuntimeError("This ISO has no sources/install.wim (an install.esd ISO cannot be used)")
        yield "STAGE Extracting the WebView2 runtime"
        _run([seven, "x", "-y", f"-o{work / 'out'}", str(wim), f"1/{_INNER.as_posix()}/*"], cancelled)
        wim.unlink()
        found = work / "out" / "1" / _INNER
        if not (found / "Application").is_dir():
            raise RuntimeError("The ISO's Windows image has no WebView2 runtime")
        target = root()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(found), str(target))
    yield f"STAGE WebView2 runtime {version()} ready"
