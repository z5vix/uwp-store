"""User settings, one JSON file under XDG_CONFIG_HOME."""

from __future__ import annotations

import json
import threading

from . import paths

DEFAULTS = {
    "market": "US",
    "locale": "en-US",
    "runtime": "",                # id of the selected runtime (see runtimes.discover)
    "game_runtime": "",           # runtime used for products in the Games family; empty = same as runtime
    "iso_path": "",               # the user's own Windows 11 ISO; never copied or shipped
    "windows_tree": "",           # a harvested tree to adopt instead of harvesting again
    "keep_downloads": False,
    "msa_refresh_token": "",     # only when no keyring is available (file is mode 0600)
    "msa_profile": {},            # gamertag/xuid of the signed-in account; never tokens
}

_lock = threading.Lock()
_cache: dict | None = None


def load() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            data = dict(DEFAULTS)
            try:
                data.update(json.loads(paths.config_file().read_text()))
            except (OSError, ValueError):
                pass
            _cache = data
        return dict(_cache)


def save(**changes: object) -> dict:
    global _cache
    current = load()
    current.update(changes)
    path = paths.config_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)
    with _lock:
        _cache = current
    return dict(current)
