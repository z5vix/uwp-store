"""Which Microsoft account the Xbox app (and the games it starts) is signed in as, one runtime at a time.

Wine-UWP's winexboxauth keeps the Xbox app's sign-in in the prefix, in %LOCALAPPDATA%\\wineuwp\\xboxauth.bin
(DPAPI-protected, one account; it also holds the prefix's device key). Games started from the Xbox app read the
same file for their licence check and Xbox Live sign-in (their environment is cleaned, so the file's default
location is the only one they all share).

To let several accounts use one runtime, the store keeps one copy of that file per account in a vault beside the
profile (<profile>/xbox-accounts/<xuid>.bin) and swaps the wanted account's copy into place while the Xbox app is
closed. An account with no copy yet gets an empty slot, so the Xbox app asks it to sign in (once).

Whose sign-in is in place: the store's own record of the last swap, unless the Xbox app has since signed in as
someone else, which winexboxauth publishes as HKCU\\Software\\Microsoft\\XboxLive\\Xuid.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import runtimes


class XboxAccountError(Exception):
    pass


def _user_dir(runtime: runtimes.Runtime) -> Path:
    users = runtime.prefix / "drive_c" / "users"
    for name in (getpass.getuser(), "steamuser"):
        if (users / name).is_dir():
            return users / name
    others = [p for p in users.iterdir() if p.is_dir() and p.name != "Public"] if users.is_dir() else []
    return others[0] if others else users / getpass.getuser()


def auth_file(runtime: runtimes.Runtime) -> Path:
    return _user_dir(runtime) / "AppData" / "Local" / "wineuwp" / "xboxauth.bin"


def vault(runtime: runtimes.Runtime) -> Path:
    folder = runtime.profile / "xbox-accounts"
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    return folder


def _marker(runtime: runtimes.Runtime) -> Path:
    return vault(runtime) / "active.json"


def _registry_xuid(runtime: runtimes.Runtime) -> tuple[str, float]:
    """HKCU\\Software\\Microsoft\\XboxLive\\Xuid from the prefix's user.reg, and when that file last changed."""
    path = runtime.prefix / "user.reg"
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return "", 0.0
    match = re.search(r'^\[Software\\\\Microsoft\\\\XboxLive\][^\n]*\n(.*?)(?:\n\n|\n\[)', text, re.S | re.M)
    xuid = ""
    if match:
        found = re.search(r'^"Xuid"="([^"]*)"', match.group(1), re.M)
        xuid = found.group(1) if found else ""
    return xuid, path.stat().st_mtime


def xbox_app(runtime: runtimes.Runtime) -> runtimes.InstalledApp | None:
    return runtime.find_app(runtimes.XBOX_APP_FAMILY)


def signed_in_as(runtime: runtimes.Runtime) -> str:
    """The xuid whose sign-in is in place ("" when the Xbox app is signed out here)."""
    if not auth_file(runtime).is_file():
        return ""
    try:
        record = json.loads(_marker(runtime).read_text())
    except (OSError, ValueError):
        record = {}
    xuid, changed = _registry_xuid(runtime)
    if xuid and (not record.get("xuid") or (xuid != record["xuid"] and changed > record.get("time", 0))):
        return xuid                                  # the Xbox app signed in as someone else since the last swap
    return record.get("xuid", "") or xuid


def known(runtime: runtimes.Runtime) -> set[str]:
    """Accounts with a saved Xbox app sign-in for this runtime (plus the one in place)."""
    saved = {p.stem for p in vault(runtime).glob("*.bin")}
    current = signed_in_as(runtime)
    return saved | ({current} if current else set())


def busy(runtime: runtimes.Runtime) -> bool:
    """Is the Xbox app (or anything else) running in this runtime's prefix?"""
    app = xbox_app(runtime)
    if app and runtime.running(app):
        return True
    # games the Xbox app starts are not in the stage list: look for any process using this prefix
    wanted = f"WINEPREFIX={runtime.prefix}".encode()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            environ = (proc / "environ").read_bytes()
        except OSError:
            continue
        if wanted in environ.split(b"\0"):
            return True
    return False


def _save_current(runtime: runtimes.Runtime) -> None:
    source = auth_file(runtime)
    owner = signed_in_as(runtime)
    if source.is_file() and owner:
        target = vault(runtime) / f"{owner}.bin"
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)


def switch(runtime: runtimes.Runtime, xuid: str) -> bool:
    """Put this account's Xbox app sign-in in place. False if it was already; raises while the app is running.

    An account without a saved sign-in leaves the slot empty: the Xbox app then asks that account to sign in.
    """
    if signed_in_as(runtime) == xuid and auth_file(runtime).is_file():
        return False
    if busy(runtime):
        raise XboxAccountError("Close the Xbox app (and its games) first: it is signed in as another account")
    _save_current(runtime)
    target = auth_file(runtime)
    saved = vault(runtime) / f"{xuid}.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    if saved.is_file():
        shutil.copyfile(saved, target)
    else:
        target.unlink(missing_ok=True)
    _marker(runtime).write_text(json.dumps({"xuid": xuid, "time": time.time()}))
    return True


def adopt(runtime: runtimes.Runtime, xuid: str) -> bool:
    """Before the store signs this account in itself: True when its sign-in is already in place (from the vault or
    because it is the current one); False when the caller must import it. Unlike switch, it never empties the slot,
    because the file also holds the prefix's Xbox device identity, and a new one takes another device slot."""
    if signed_in_as(runtime) == xuid and auth_file(runtime).is_file():
        return True
    if busy(runtime):
        raise XboxAccountError("Close the Xbox app (and its games) first: it is signed in as another account")
    _save_current(runtime)
    saved = vault(runtime) / f"{xuid}.bin"
    if saved.is_file():
        target = auth_file(runtime)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(saved, target)
        _marker(runtime).write_text(json.dumps({"xuid": xuid, "time": time.time()}))
        return True
    return False


def mark_signed_in(runtime: runtimes.Runtime, xuid: str) -> None:
    """The store put this account's sign-in in place itself (gdk.sign_in): record it, and save its vault copy."""
    _marker(runtime).write_text(json.dumps({"xuid": xuid, "time": time.time()}))
    _save_current(runtime)


def sign_out_here(runtime: runtimes.Runtime) -> None:
    """Leave the Xbox app signed out in this runtime (the account's sign-in stays saved for later)."""
    if busy(runtime):
        raise XboxAccountError("Close the Xbox app (and its games) first")
    _save_current(runtime)
    auth_file(runtime).unlink(missing_ok=True)
    _marker(runtime).unlink(missing_ok=True)


def forget(runtime: runtimes.Runtime, xuid: str) -> None:
    """Drop an account's saved Xbox app sign-in (and sign the Xbox app out if it is the one in place)."""
    (vault(runtime) / f"{xuid}.bin").unlink(missing_ok=True)
    if signed_in_as(runtime) == xuid and not busy(runtime):
        auth_file(runtime).unlink(missing_ok=True)
        _marker(runtime).unlink(missing_ok=True)


def forget_everywhere(xuid: str) -> None:
    """An account signed out of the store: drop its Xbox app sign-in from every runtime."""
    for runtime in runtimes_with_xbox_app():
        forget(runtime, xuid)


def prepare_for(runtime: runtimes.Runtime, xuid: str) -> str:
    """Before opening the Xbox app for a game an account owns: put that account in place if the app is closed.

    Returns a message for the user ("" when nothing needs saying); raises XboxAccountError when the Xbox app is
    running as someone else.
    """
    if not xuid or signed_in_as(runtime) == xuid:
        return ""
    switch(runtime, xuid)
    if not (vault(runtime) / f"{xuid}.bin").is_file() and not auth_file(runtime).is_file():
        return "sign-in"                              # this account has not signed in to the Xbox app here yet
    return "switched"


def runtimes_with_xbox_app() -> list[runtimes.Runtime]:
    return [rt for rt in runtimes.discover() if xbox_app(rt)]
