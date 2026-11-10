"""Small HTTP layer: a shared opener, a disk cache for catalog JSON and images, and streaming downloads."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from . import paths

USER_AGENT = "uwp-store/2.0 (Linux)"
_lock = threading.Lock()

# The Store's delivery hosts chain to "Microsoft Root Certificate Authority 2011", which Windows trusts
# but Linux CA bundles do not carry. That root (SHA-256 847DF6A7...ED7C61) is added for those hosts
# only; every other host is verified against the system store alone. Verification is never disabled.
_MS_ROOT = Path(__file__).with_name("microsoft-root-2011.pem")
_MS_HOSTS = (".delivery.mp.microsoft.com",)
_ms_context: ssl.SSLContext | None = None


def _context_for(url: str) -> ssl.SSLContext | None:
    global _ms_context
    host = urllib.parse.urlparse(url).hostname or ""
    if not host.endswith(_MS_HOSTS):
        return None
    if _ms_context is None:
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=str(_MS_ROOT))
        _ms_context = context
    return _ms_context


def _open(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout, context=_context_for(request.full_url))


class NetError(Exception):
    pass


def _request(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> urllib.request.Request:
    merged = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    merged.update(headers or {})
    return urllib.request.Request(url, data=data, headers=merged, method="POST" if data is not None else "GET")


def get(url: str, headers: dict[str, str] | None = None, timeout: float = 30) -> bytes:
    try:
        with _open(_request(url, None, headers), timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise NetError(f"HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NetError(f"Network error for {url}: {exc}") from exc


def post(url: str, data: bytes, headers: dict[str, str], timeout: float = 30) -> tuple[int, bytes]:
    try:
        with _open(_request(url, data, headers), timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NetError(f"Network error for {url}: {exc}") from exc


def _cache_path(kind: str, key: str, suffix: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()
    folder = paths.cache_dir() / kind / digest[:2]
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (digest + suffix)


def get_json(url: str, max_age: float = 1800, headers: dict[str, str] | None = None) -> object:
    """GET a JSON document, served from the disk cache while it is younger than max_age seconds.

    A stale cached copy is still returned when the network is down, so the store opens offline.
    """
    path = _cache_path("json", url, ".json")
    fresh = path.exists() and time.time() - path.stat().st_mtime < max_age
    if fresh:
        try:
            return json.loads(path.read_bytes())
        except ValueError:
            pass
    try:
        data = get(url, {"Accept": "application/json", **(headers or {})})
        parsed = json.loads(data)
    except (NetError, ValueError):
        if path.exists():
            return json.loads(path.read_bytes())
        raise
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return parsed


def cached_file(url: str, suffix: str = ".img") -> Path:
    """Fetch a small file (an image) once and keep it in the cache."""
    path = _cache_path("img", url, suffix)
    if path.exists() and path.stat().st_size:
        return path
    data = get(url, timeout=20)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def download(url: str, target: Path, *, expected_size: int = 0,
             progress: Callable[[int, int], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> Path:
    """Stream url to target through a .part file, resuming a partial download when the server allows."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    try:
        response = _open(_request(url, None, headers), 60)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and expected_size and offset == expected_size:
            part.replace(target)
            return target
        raise NetError(f"HTTP {exc.code} downloading {target.name}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NetError(f"Network error downloading {target.name}: {exc}") from exc
    with response:
        if offset and response.status != 206:
            offset = 0  # server ignored the range: start over
        total = expected_size or (int(response.headers.get("Content-Length", 0)) + offset)
        done = offset
        with part.open("ab" if offset else "wb") as stream:
            while True:
                if cancelled and cancelled():
                    raise NetError("Download cancelled")
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                stream.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    if expected_size and done != expected_size:
        raise NetError(f"{target.name}: got {done} bytes, expected {expected_size}")
    part.replace(target)
    return target


def verify_digest(path: Path, digest_b64: str, algorithm: str = "SHA1") -> bool:
    import base64
    algo = {"SHA1": "sha1", "SHA256": "sha256"}.get(algorithm.upper().replace("-", ""), "sha1")
    hasher = hashlib.new(algo)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            hasher.update(block)
    return base64.b64encode(hasher.digest()).decode() == digest_b64


def clear_cache() -> None:
    with _lock:
        shutil.rmtree(paths.cache_dir() / "json", ignore_errors=True)
