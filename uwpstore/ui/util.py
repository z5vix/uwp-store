"""Threading and image helpers for the GTK front end. GTK is only touched on the main thread."""

from __future__ import annotations

import threading
import traceback
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from gi.repository import Gdk, GLib, Gtk

from .. import net

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ui-io")
_image_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ui-img")


def run_async(work: Callable[[], Any], done: Callable[[Any], None] | None = None,
              failed: Callable[[Exception], None] | None = None) -> None:
    """Run work on a pool thread, then done(result) or failed(exc) on the main loop."""
    def task() -> None:
        try:
            result = work()
        except Exception as exc:  # delivered to the UI, never swallowed silently
            if not isinstance(exc, (net.NetError, OSError, ValueError)):
                traceback.print_exc()
            if failed:
                GLib.idle_add(_call, failed, exc)
            return
        if done:
            GLib.idle_add(_call, done, result)
    _pool.submit(task)


def _call(fn: Callable, arg: Any) -> bool:
    fn(arg)
    return GLib.SOURCE_REMOVE


def on_main(fn: Callable, *args: Any) -> None:
    GLib.idle_add(lambda: (fn(*args), GLib.SOURCE_REMOVE)[1])


class _TextureCache:
    def __init__(self, limit: int = 400) -> None:
        self.items: "OrderedDict[str, Gdk.Texture]" = OrderedDict()
        self.limit = limit
        self.lock = threading.Lock()

    def get(self, key: str) -> Gdk.Texture | None:
        with self.lock:
            texture = self.items.get(key)
            if texture is not None:
                self.items.move_to_end(key)
            return texture

    def put(self, key: str, texture: Gdk.Texture) -> None:
        with self.lock:
            self.items[key] = texture
            self.items.move_to_end(key)
            while len(self.items) > self.limit:
                self.items.popitem(last=False)


_textures = _TextureCache()


def load_image(picture: Gtk.Picture | Gtk.Image, url: str | None) -> None:
    """Fill a Picture (or Image) from a URL through the disk cache, without blocking the UI."""
    if not url:
        return
    cached = _textures.get(url)
    if cached is not None:
        _apply(picture, cached)
        return
    picture._wanted_url = url  # type: ignore[attr-defined]

    def work() -> None:
        try:
            path = net.cached_file(url)
            texture = Gdk.Texture.new_from_filename(str(path))
        except Exception:
            return
        _textures.put(url, texture)
        GLib.idle_add(_deliver, picture, url, texture)
    _image_pool.submit(work)


def _deliver(picture, url: str, texture: Gdk.Texture) -> bool:
    if getattr(picture, "_wanted_url", None) == url:
        _apply(picture, texture)
    return GLib.SOURCE_REMOVE


def _apply(picture, texture: Gdk.Texture) -> None:
    if isinstance(picture, Gtk.Picture):
        picture.set_paintable(texture)
    else:
        picture.set_from_paintable(texture)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def stars(rating: float) -> str:
    if rating <= 0:
        return ""
    return f"★ {rating:.1f}"


def clear(box: Gtk.Widget) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt
