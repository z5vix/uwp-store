"""Settings and first-run setup: runtimes, the user's Windows 11 ISO, prefixes, the GE-Proton UWP build."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterator

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from .. import account, catalog, installer, net, packages, paths, runtimes, settings
from . import account as ui_account
from .util import human_size, run_async

REPO = Path(__file__).resolve().parents[2]
PROTON_PIPELINE = REPO / "proton-uwp"
EVAL_ISO_PAGE = "https://www.microsoft.com/evalcenter/download-windows-11-enterprise"


class TaskDialog(Adw.Dialog):
    """Runs a generator of output lines on a thread and shows STAGE lines and a live log."""

    def __init__(self, title: str, work: Callable[[Callable[[], bool]], Iterator[str]],
                 on_done: Callable[[bool], None] | None = None) -> None:
        super().__init__(title=title, content_width=640, content_height=460)
        self.cancelled = False
        self.on_done = on_done
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(18)
        self.stage = Gtk.Label(label="Starting…", xalign=0, wrap=True)
        self.stage.add_css_class("title-4")
        box.append(self.stage)
        self.bar = Gtk.ProgressBar()
        box.append(self.bar)
        self.buffer = Gtk.TextBuffer()
        text = Gtk.TextView(buffer=self.buffer, editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        scroller = Gtk.ScrolledWindow(vexpand=True, child=text)
        scroller.add_css_class("card")
        self.scroller = scroller
        box.append(scroller)
        self.button = Gtk.Button(label="Cancel", halign=Gtk.Align.END)
        self.button.connect("clicked", self._button)
        box.append(self.button)
        view.set_content(box)
        self.set_child(view)
        self.finished = False
        self._pulse = GLib.timeout_add(150, self._tick)
        threading.Thread(target=self._run, args=(work,), daemon=True).start()

    def _tick(self) -> bool:
        if self.finished:
            return GLib.SOURCE_REMOVE
        self.bar.pulse()
        return GLib.SOURCE_CONTINUE

    def _button(self, _b) -> None:
        if self.finished:
            self.close()
        else:
            self.cancelled = True
            self.button.set_sensitive(False)
            self.stage.set_label("Cancelling…")

    def _run(self, work) -> None:
        ok = True
        try:
            for line in work(lambda: self.cancelled):
                GLib.idle_add(self._line, line)
        except Exception as exc:
            ok = False
            GLib.idle_add(self._line, f"ERROR {exc}")
        GLib.idle_add(self._finish, ok)

    def _line(self, line: str) -> bool:
        if line.startswith("STAGE "):
            self.stage.set_label(line[6:])
        elif line.startswith("READY "):
            self.stage.set_label("Done")
        elif line.startswith("ERROR "):
            self.stage.set_label(line[6:])
        self.buffer.insert(self.buffer.get_end_iter(), line + "\n")
        adj = self.scroller.get_vadjustment()
        adj.set_value(adj.get_upper())
        return GLib.SOURCE_REMOVE

    def _finish(self, ok: bool) -> bool:
        self.finished = True
        self.bar.set_fraction(1.0 if ok else 0.0)
        if ok and not self.stage.get_label().startswith("Done"):
            self.stage.set_label("Done")
        self.button.set_label("Close")
        self.button.set_sensitive(True)
        if self.on_done:
            self.on_done(ok)
        return GLib.SOURCE_REMOVE


def _script_lines(argv: list[str], cancelled: Callable[[], bool]) -> Iterator[str]:
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                               start_new_session=True)
    assert process.stdout is not None
    for line in process.stdout:
        if cancelled():
            process.terminate()
            raise RuntimeError("Cancelled")
        yield line.rstrip("\n")
    if process.wait() != 0:
        raise RuntimeError(f"{Path(argv[0]).name} failed (exit {process.returncode})")


class SettingsDialog(Adw.PreferencesDialog):
    def __init__(self, toast: Callable[[str], None], changed: Callable[[], None]) -> None:
        super().__init__(title="Settings", search_enabled=False)
        self.toast = toast
        self.changed = changed
        self._build()

    def _build(self) -> None:
        for page in list(getattr(self, "_pages", [])):
            self.remove(page)
        self._pages = []
        cfg = settings.load()
        found = runtimes.discover()

        page = Adw.PreferencesPage(title="Setup", icon_name="emblem-system-symbolic")
        self._pages.append(page)
        self.add(page)

        # 1. Windows components
        tree = runtimes.shared_windows_tree()
        windows = Adw.PreferencesGroup(
            title="1. Windows components",
            description=("UWP apps need Microsoft's own frameworks (XAML, the app model), taken from a Windows 11 "
                         "ISO you provide. The ISO is read once on this computer and is never copied, shipped or "
                         "uploaded."))
        status = Adw.ActionRow(title="Harvested components", subtitle=str(tree) if tree else "Not harvested yet")
        status.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic" if tree else "dialog-warning-symbolic"))
        windows.add(status)
        iso_row = Adw.ActionRow(title="Windows 11 ISO", subtitle=cfg.get("iso_path") or "None chosen",
                                subtitle_lines=1)
        choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", lambda *_: self._choose_iso())
        iso_row.add_suffix(choose)
        harvest = Gtk.Button(label="Harvest", valign=Gtk.Align.CENTER, sensitive=bool(cfg.get("iso_path") and found))
        harvest.add_css_class("suggested-action")
        harvest.connect("clicked", lambda *_: self._harvest())
        iso_row.add_suffix(harvest)
        windows.add(iso_row)
        link = Adw.ActionRow(title="No ISO?", subtitle="Microsoft offers a free Windows 11 Enterprise evaluation ISO",
                             activatable=True)
        link.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
        link.connect("activated", lambda *_: Gtk.UriLauncher(uri=EVAL_ISO_PAGE).launch(self.get_root(), None, None, None))
        windows.add(link)
        page.add(windows)

        # 2. Runtimes
        group = Adw.PreferencesGroup(title="2. Runtimes",
                                     description="Each runtime keeps its own Wine prefix. Apps install into the app "
                                                 "runtime, games into the game runtime.")
        if not found:
            group.add(Adw.ActionRow(title="No runtime found", subtitle="Build GE-Proton UWP below, or build wine-uwp"))
        for runtime in found:
            row = Adw.ActionRow(title=runtime.name, subtitle=self._runtime_state(runtime), subtitle_lines=2)
            row.add_prefix(Gtk.Image(icon_name="applications-games-symbolic" if runtime.is_proton else "application-x-executable-symbolic"))
            if runtime.usable() and not runtime.prefix_ready():
                make = Gtk.Button(label="Create prefix", valign=Gtk.Align.CENTER, sensitive=tree is not None)
                make.add_css_class("suggested-action")
                make.connect("clicked", lambda _b, r=runtime: self._new_prefix(r))
                row.add_suffix(make)
            group.add(row)
        page.add(group)

        if found:
            choice = Adw.PreferencesGroup(title="Defaults")
            names = Gtk.StringList.new([r.name for r in found])
            current = runtimes.default_runtime()
            app_row = Adw.ComboRow(title="Apps run with", model=names,
                                   selected=max(0, next((i for i, r in enumerate(found) if current and r.id == current.id), 0)))
            app_row.connect("notify::selected", lambda row, _p: self._pick("runtime", found[row.get_selected()]))
            choice.add(app_row)
            game = runtimes.game_runtime()
            game_row = Adw.ComboRow(title="Games run with", model=names,
                                    selected=max(0, next((i for i, r in enumerate(found) if game and r.id == game.id), 0)))
            game_row.connect("notify::selected", lambda row, _p: self._pick("game_runtime", found[row.get_selected()]))
            choice.add(game_row)
            page.add(choice)

        # 3. GE-Proton UWP
        proton = Adw.PreferencesGroup(
            title="GE-Proton UWP (for games)",
            description=("wine-uwp ported onto GE-Proton's Wine, with GE's DXVK, vkd3d-proton and game fixes. "
                         "Built locally from source by proton-uwp/ in this repository; it takes a while."))
        status_file = PROTON_PIPELINE / "PORT-STATUS.md"
        port_state = "Not started"
        if status_file.exists():
            for line in status_file.read_text().splitlines():
                if line.startswith("| still conflicted"):
                    port_state = "Port: " + line.strip("| ").replace(" | ", ": ")
        built = any(r.is_proton for r in found)
        prow = Adw.ActionRow(title="Status", subtitle="Built and installed" if built else port_state)
        proton.add(prow)
        build = Adw.ButtonRow(title="Rebuild GE-Proton UWP" if built else "Build GE-Proton UWP")
        build.connect("activated", lambda *_: self._build_proton())
        proton.add(build)
        page.add(proton)

        # Store preferences
        store = Adw.PreferencesPage(title="Store", icon_name="preferences-system-symbolic")
        self._pages.append(store)
        self.add(store)
        store.add(self._account_group())
        store.add(self._store_app_group(found))
        region = Adw.PreferencesGroup(title="Region")
        market = Adw.EntryRow(title="Market (country code)", text=cfg["market"], show_apply_button=True)
        market.connect("apply", lambda row: self._save(market=row.get_text().strip().upper() or "US"))
        region.add(market)
        locale = Adw.EntryRow(title="Language (locale)", text=cfg["locale"], show_apply_button=True)
        locale.connect("apply", lambda row: self._save(locale=row.get_text().strip() or "en-US"))
        region.add(locale)
        store.add(region)
        storage = Adw.PreferencesGroup(title="Storage")
        keep = Adw.SwitchRow(title="Keep downloaded packages", subtitle=str(paths.downloads_dir()),
                             active=bool(cfg.get("keep_downloads")))
        keep.connect("notify::active", lambda row, _p: self._save(keep_downloads=row.get_active()))
        storage.add(keep)
        clear = Adw.ButtonRow(title="Clear catalogue cache")
        clear.connect("activated", lambda *_: (net.clear_cache(), self.toast("Catalogue cache cleared")))
        storage.add(clear)
        store.add(storage)

    def _account_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Microsoft account",
            description="Sign-in uses Microsoft's device-code page in your browser. The store keeps only a refresh "
                        "token, in your keyring. It is used for licensed downloads and your gamertag.")
        status = Adw.ActionRow(title=ui_account.status_text())
        group.add(status)
        if account.signed_in():
            out = Adw.ButtonRow(title="Sign out")
            out.add_css_class("destructive-action")
            out.connect("activated", lambda *_: (account.sign_out(), self.changed(), self._build(),
                                                 self.toast("Signed out")))
            group.add(out)
        else:
            sign = Adw.ButtonRow(title="Sign in with Microsoft…")
            sign.add_css_class("suggested-action")

            def done(profile: account.Profile) -> None:
                self.changed()
                self._build()
                self.toast(f"Signed in as {profile.gamertag or 'your Microsoft account'}")
            sign.connect("activated", lambda *_: ui_account.SignInDialog(done).present(self))
            group.add(sign)
        return group

    def _store_app_group(self, found: list[runtimes.Runtime]) -> Adw.PreferencesGroup:
        """Microsoft's own Store app (9WZDNCRFJBMP) in the runtimes, against the newest the delivery service offers."""
        group = Adw.PreferencesGroup(
            title="Microsoft Store app",
            description="Microsoft's own Store app, for running inside a runtime. The newest version comes straight "
                        "from Microsoft's delivery service, which also serves the packages store.rg-adguard.net lists.")
        latest = Adw.ActionRow(title="Latest version", subtitle="Checking…")
        group.add(latest)
        rows: dict[str, Adw.ActionRow] = {}
        for runtime in found:
            row = Adw.ActionRow(title=runtime.name, subtitle="…")
            rows[runtime.id] = row
            group.add(row)

        def show(result) -> None:
            product, offer = result
            pkg = offer.package
            latest.set_subtitle(f"{pkg.version} · {pkg.extension} · {human_size(pkg.size)}")
            installed = {rt.id: app for rt, app in offer.installed}
            for runtime in found:
                row = rows[runtime.id]
                app = installed.get(runtime.id)
                outdated = app is not None and app.version and \
                    packages.version_tuple(app.version) < packages.version_tuple(pkg.version)
                row.set_subtitle(f"{app.version} installed" + (" · update available" if outdated else " · up to date")
                                 if app else "Not installed")
                if (outdated or app is None) and runtime.ready():
                    button = Gtk.Button(label="Update" if app else "Install", valign=Gtk.Align.CENTER)
                    if outdated:
                        button.add_css_class("suggested-action")
                    button.connect("clicked", lambda b, rt=runtime: self._install_store(product, rt, b))
                    row.add_suffix(button)
        run_async(lambda: (lambda p: (p, installer.offer(p)))(catalog.product(installer.STORE_PRODUCT_ID)), show,
                  lambda e: latest.set_subtitle(f"Could not check: {e}"))
        return group

    def _install_store(self, product, runtime: runtimes.Runtime, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        button.set_label("Queued")
        logo = catalog.image_url(catalog.pick_image(product.images, ("logo", "tile")), 192) or ""
        installer.get().enqueue(product.product_id, product.title, logo, False, runtime.id)
        self.toast(f"Getting Microsoft Store for {runtime.name}: see Downloads")

    @staticmethod
    def _runtime_state(runtime: runtimes.Runtime) -> str:
        parts = [runtime.wine_version or str(runtime.root)]
        if not runtime.usable():
            parts.append("incomplete install")
        elif runtime.prefix_ready():
            parts.append("ready")
        else:
            parts.append("needs a prefix")
        return " · ".join(parts)

    def _save(self, **changes) -> None:
        settings.save(**changes)
        self.changed()

    def _pick(self, key: str, runtime: runtimes.Runtime) -> None:
        settings.save(**{key: runtime.id})
        self.changed()

    def _choose_iso(self) -> None:
        dialog = Gtk.FileDialog(title="Choose a Windows 11 ISO")
        filt = Gtk.FileFilter(name="Disc images")
        filt.add_pattern("*.iso")
        filt.add_pattern("*.ISO")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dialog.set_filters(store)

        def done(dlg, result) -> None:
            try:
                file = dlg.open_finish(result)
            except GLib.Error:
                return
            settings.save(iso_path=file.get_path())
            self._build()
        dialog.open(self.get_root(), None, done)

    def _harvest(self) -> None:
        runtime = runtimes.default_runtime() or (runtimes.discover() or [None])[0]
        iso = Path(settings.load()["iso_path"])
        if runtime is None or not iso.is_file():
            self.toast("Choose an ISO and have a runtime first")
            return
        TaskDialog("Harvesting Windows components", lambda c: runtime.harvest(iso, c),
                   lambda ok: (self._build(), self.changed())).present(self)

    def _new_prefix(self, runtime: runtimes.Runtime) -> None:
        TaskDialog(f"Creating prefix for {runtime.name}", lambda c: runtime.new_prefix(c),
                   lambda ok: (self._build(), self.changed())).present(self)

    def _build_proton(self) -> None:
        def steps(cancelled) -> Iterator[str]:
            for script in ("01-fetch.sh", "02-prep-wine.sh"):
                yield from _script_lines([str(PROTON_PIPELINE / script)], cancelled)
            yield from _script_lines(["python3", str(PROTON_PIPELINE / "03-port.py")], cancelled)
            for script in ("04-build.sh", "06-package.sh"):
                yield from _script_lines([str(PROTON_PIPELINE / script)], cancelled)
        TaskDialog("Building GE-Proton UWP", steps, lambda ok: (self._build(), self.changed())).present(self)
