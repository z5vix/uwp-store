"""Storefront pages: browse (home/apps/gaming), search, product, library."""

from __future__ import annotations

import base64
import subprocess
from typing import Callable

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from .. import account, catalog, fe3, installer, packages, runtimes
from . import widgets
from .util import clear, human_size, load_image, run_async, stars

OpenProduct = Callable[[str, str], None]


# ---- browse ------------------------------------------------------------------------------------

APPS_SECTIONS = [
    ("Trending apps", "toptrending", "Apps"),
    ("Top free apps", "TopFree", "Apps"),
    ("Top paid apps", "TopPaid", "Apps"),
    ("Best selling apps", "TopGrossing", "Apps"),
    ("Best rated apps", "BestRated", "Apps"),
    ("New and rising", "NewAndRising", "Apps"),
]
GAMING_FALLBACK = [
    ("Trending games", "toptrending", "Games"),
    ("Top free games", "TopFree", "Games"),
    ("Best selling games", "TopGrossing", "Games"),
    ("Top paid games", "TopPaid", "Games"),
    ("Best rated games", "BestRated", "Games"),
]


class BrowsePage(Gtk.Box):
    """Sections loaded lazily: the page layout first, then every section's cards in parallel."""

    def __init__(self, page: str, open_product: OpenProduct, open_list: Callable[[str, catalog.Section], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.page = page
        self.open_product = open_product
        self.open_list = open_list
        self.loaded = False

    def ensure_loaded(self) -> None:
        if not self.loaded:
            self.loaded = True
            self.reload()

    def reload(self) -> None:
        clear(self)
        self.append(widgets.loading())

        def layout() -> list[catalog.Section]:
            if self.page == "apps":
                return [catalog.Section(t, "row", f"/recommendations/collections/{n}?{catalog._params(mediaType=m, pageSize='25', cardsEnabled='true')}")
                        for t, n, m in APPS_SECTIONS]
            sections = catalog.page_sections("gaminghome" if self.page == "gaming" else self.page)
            if self.page == "gaming" and not sections:
                sections = [catalog.Section(t, "row", f"/recommendations/collections/{n}?{catalog._params(mediaType=m, pageSize='25', cardsEnabled='true')}")
                            for t, n, m in GAMING_FALLBACK]
            return sections
        run_async(layout, self._show_layout, lambda e: self._failed(str(e)))

    def _failed(self, message: str) -> None:
        clear(self)
        self.append(widgets.error_page(message, self.reload))

    def _show_layout(self, sections: list[catalog.Section]) -> None:
        clear(self)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=28)
        body.set_margin_top(18)
        body.set_margin_bottom(24)
        body.set_margin_start(18)
        body.set_margin_end(18)
        self.append(widgets.page_scroller(body))
        for section in sections:
            slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            body.append(slot)
            run_async(lambda s=section: catalog.load_section(s),
                      lambda s, slot=slot: self._fill(slot, s),
                      lambda _e, slot=slot: slot.set_visible(False))

    def _fill(self, slot: Gtk.Box, section: catalog.Section) -> None:
        cards = [c for c in section.cards if c.is_product]
        if not cards:
            slot.set_visible(False)
            return
        if section.layout == "hero":
            slot.append(widgets.Spotlight(cards, self.open_product))
            return
        title = section.title if section.title and "/" not in section.title else "Featured"
        slot.append(widgets.CardRow(title, cards, self.open_product, lambda: self.open_list(title, section)))


class ListPage(Gtk.Box):
    def __init__(self, cards: list[catalog.Card], open_product: OpenProduct) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        grid = widgets.CardGrid(cards, open_product)
        grid.set_margin_top(18)
        grid.set_margin_bottom(18)
        grid.set_margin_start(18)
        grid.set_margin_end(18)
        self.append(widgets.page_scroller(grid))


# ---- search ------------------------------------------------------------------------------------

class SearchPage(Gtk.Box):
    def __init__(self, open_product: OpenProduct) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.open_product = open_product
        self.query = ""
        self.media = None
        filters = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        filters.set_margin_top(12)
        filters.add_css_class("linked")
        first = None
        for label, media in (("All", None), ("Apps", "apps"), ("Games", "games")):
            toggle = Gtk.ToggleButton(label=label, active=media is None)
            if first is None:
                first = toggle
            else:
                toggle.set_group(first)
            toggle.connect("toggled", lambda b, m=media: b.get_active() and self._set_media(m))
            filters.append(toggle)
        self.append(filters)
        self.results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.append(self.results)
        self._empty()

    def _empty(self) -> None:
        clear(self.results)
        self.results.append(Adw.StatusPage(icon_name="system-search-symbolic", title="Search the Store",
                                           description="Apps and games, by name or publisher", vexpand=True))

    def _set_media(self, media: str | None) -> None:
        self.media = media
        if self.query:
            self.search(self.query)

    def search(self, query: str) -> None:
        self.query = query.strip()
        if not self.query:
            self._empty()
            return
        clear(self.results)
        self.results.append(widgets.loading("Searching…"))
        wanted = (self.query, self.media)
        run_async(lambda: catalog.search(self.query, self.media),
                  lambda cards: wanted == (self.query, self.media) and self._show(cards),
                  lambda e: self._show_error(str(e)))

    def _show_error(self, message: str) -> None:
        clear(self.results)
        self.results.append(widgets.error_page(message, lambda: self.search(self.query)))

    def _show(self, cards: list[catalog.Card]) -> None:
        clear(self.results)
        cards = [c for c in cards if c.is_product]
        if not cards:
            self.results.append(Adw.StatusPage(icon_name="edit-find-symbolic", title="No results",
                                               description=f"Nothing matched “{self.query}”", vexpand=True))
            return
        grid = widgets.CardGrid(cards, self.open_product)
        grid.set_margin_top(12)
        grid.set_margin_start(18)
        grid.set_margin_end(18)
        grid.set_margin_bottom(18)
        self.results.append(widgets.page_scroller(grid))


# ---- product -----------------------------------------------------------------------------------

class ProductPage(Gtk.Box):
    def __init__(self, product_id: str, toast: Callable[[str], None], open_settings: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.product_id = product_id
        self.toast = toast
        self.open_settings = open_settings
        self.product: catalog.Product | None = None
        self.job: installer.Job | None = None
        self.offer: installer.Offer | None = None
        self.resolution: fe3.Resolution | None = None
        self.append(widgets.loading())
        run_async(lambda: catalog.product(product_id), self._show, lambda e: self._failed(str(e)))
        unsubscribe = installer.get().subscribe(self._job_changed)
        self.connect("destroy", lambda *_: unsubscribe())
        self.connect("unrealize", lambda *_: unsubscribe())

    def _failed(self, message: str) -> None:
        clear(self)
        self.append(widgets.error_page(message))

    def _show(self, product: catalog.Product) -> None:
        self.product = product
        clear(self)
        nav_page = self.get_ancestor(Adw.NavigationPage)
        if nav_page is not None:
            nav_page.set_title(product.title)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        for side in ("top", "bottom", "start", "end"):
            getattr(body, f"set_margin_{side}")(24)

        # header: logo, title, publisher, rating, action
        header = Gtk.Box(spacing=24)
        logo = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        logo.set_size_request(128, 128)
        logo.add_css_class("store-art")
        load_image(logo, catalog.image_url(catalog.pick_image(product.images, ("logo", "tile", "BoxArt")), 256))
        header.append(logo)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER, hexpand=True)
        title = Gtk.Label(label=product.title, xalign=0, wrap=True, selectable=True)
        title.add_css_class("title-1")
        info.append(title)
        publisher = Gtk.Label(label=product.publisher, xalign=0)
        publisher.add_css_class("dim-label")
        info.append(publisher)
        facts = " · ".join(x for x in (stars(product.rating) + (f" ({product.rating_count:,})" if product.rating_count else ""),
                                        product.category, product.age_rating) if x)
        info.append(Gtk.Label(label=facts, xalign=0, wrap=True))
        header.append(info)

        action = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        self.button = Gtk.Button()
        self.button.add_css_class("pill")
        self.button.add_css_class("suggested-action")
        self.button.set_size_request(180, -1)
        self.button.connect("clicked", self._on_action)
        action.append(self.button)
        self.progress = Gtk.ProgressBar(visible=False)
        action.append(self.progress)
        self.status = Gtk.Label(xalign=0.5, wrap=True, max_width_chars=28)
        self.status.add_css_class("caption")
        self.status.add_css_class("dim-label")
        action.append(self.status)
        header.append(action)
        body.append(header)

        # screenshots
        shots = product.screenshots[:10]
        if shots:
            carousel = Adw.Carousel(spacing=12)
            carousel.set_size_request(-1, 360)
            for shot in shots:
                picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True)
                picture.add_css_class("store-shot")
                load_image(picture, catalog.image_url(shot, 1280))
                carousel.append(picture)
            body.append(carousel)
            body.append(Adw.CarouselIndicatorLines(carousel=carousel))

        # description
        if product.description:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            heading = Gtk.Label(label="Description", xalign=0)
            heading.add_css_class("title-3")
            box.append(heading)
            text = Gtk.Label(label=product.description, xalign=0, wrap=True, selectable=True,
                             wrap_mode=Pango.WrapMode.WORD_CHAR)
            box.append(text)
            body.append(box)

        # details
        details = Adw.PreferencesGroup(title="Details")
        runtime = runtimes.runtime_for(product.is_game)
        self._row(details, "Runs with", runtime.name if runtime else "No runtime set up")
        self._row(details, "Package type", self._package_kind(product))
        if product.platforms:
            self._row(details, "Architectures", ", ".join(product.platforms))
        if product.family_names:
            self._row(details, "Package family", product.family_names[0])
        if product.capabilities:
            self._row(details, "Capabilities", ", ".join(product.capabilities[:12]) +
                      (f" and {len(product.capabilities) - 12} more" if len(product.capabilities) > 12 else ""))
        for level in ("Minimum", "Recommended"):
            block = product.system_requirements.get(level) or {}
            items = [f"{i.get('Name')}: {i.get('Description')}" for i in block.get("Items") or [] if i.get("Name")]
            if items:
                self._row(details, f"{level} requirements", "\n".join(items))
        body.append(details)

        packages_group = Adw.PreferencesGroup(title="Packages",
                                              description="What the Store's delivery service offers for this product")
        expander = Adw.ExpanderRow(title="Show packages")
        expander.connect("notify::expanded", lambda row, _p: row.get_expanded() and self._load_packages(row))
        packages_group.add(expander)
        if product.category_id:
            body.append(packages_group)

        self.append(widgets.page_scroller(body))
        self.job = installer.get().active_for(product.product_id)
        self._refresh()
        if product.category_id and product.is_free and not product.is_msixvc:
            self._check_offer()

    def _check_offer(self) -> None:
        """Ask the delivery service for the newest package, to offer an update over an older install."""
        product = self.product

        def work():
            resolution = fe3.DeliveryClient(account.store_ticket()).resolve(product.category_id)
            return resolution, installer.offer(product, resolution)

        def done(result) -> None:
            self.resolution, self.offer = result
            self._refresh()
        run_async(work, done, lambda e: None)

    @staticmethod
    def _row(group: Adw.PreferencesGroup, title: str, value: str) -> None:
        row = Adw.ActionRow(title=title, subtitle=value, subtitle_selectable=True)
        row.add_css_class("property")
        group.add(row)

    @staticmethod
    def _package_kind(product: catalog.Product) -> str:
        if product.is_msixvc:
            return "MSIXVC (Xbox PC game; not installable here yet)"
        if product.installer.lower() in ("windowsupdate", ""):
            return "MSIX/AppX through Store delivery"
        return f"Publisher installer ({product.installer}), run with Wine"

    def _load_packages(self, row: Adw.ExpanderRow) -> None:
        if getattr(row, "_loaded", False) or not self.product:
            return
        row._loaded = True  # type: ignore[attr-defined]
        spinner_row = Adw.ActionRow(title="Asking the delivery service…")
        row.add_row(spinner_row)

        def done(resolution: fe3.Resolution) -> None:
            self.resolution = resolution
            row.remove(spinner_row)
            client = fe3.DeliveryClient(account.store_ticket())
            for p in sorted(resolution.files, key=lambda f: (f.is_framework, f.name.lower(), packages.version_tuple(f.version)),
                            reverse=False)[:80]:
                tag = " · framework" if p.is_framework else (" · encrypted" if p.is_encrypted else "")
                digest = ""
                if p.digest:
                    try:
                        digest = f" · {p.digest_alg.upper()} {base64.b64decode(p.digest).hex()}"
                    except ValueError:
                        pass
                item = Adw.ActionRow(title=f"{p.moniker}.{p.extension}",
                                     subtitle=f"{human_size(p.size)}{tag}{digest}", title_lines=1, subtitle_lines=2,
                                     subtitle_selectable=True)
                copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                                  tooltip_text="Copy download link")
                copy.add_css_class("flat")
                copy.connect("clicked", lambda _b, pkg=p: self._copy_link(client, pkg))
                item.add_suffix(copy)
                row.add_row(item)
            if resolution.needs_license:
                row.add_row(Adw.ActionRow(title="Some packages need a licence from a signed-in account"))
            if not resolution.files:
                row.add_row(Adw.ActionRow(title="No downloadable packages without an account"))
            web = Adw.ActionRow(title="Compare on store.rg-adguard.net",
                                subtitle="Copies this product's link; paste it there with the URL (link) option",
                                activatable=True)
            web.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
            web.connect("activated", lambda *_: self._open_rg_adguard())
            row.add_row(web)
        run_async(lambda: fe3.DeliveryClient(account.store_ticket()).resolve(self.product.category_id), done,
                  lambda e: spinner_row.set_title(f"Failed: {e}"))

    def _copy_link(self, client: fe3.DeliveryClient, package: fe3.PackageFile) -> None:
        def copied(url: str) -> None:
            self.get_clipboard().set(url)
            self.toast(f"Copied the link for {package.name} (it expires after a while)")
        run_async(lambda: client.url_for(package), copied, lambda e: self.toast(f"No link: {e}"))

    def _open_rg_adguard(self) -> None:
        self.get_clipboard().set(f"https://apps.microsoft.com/detail/{self.product.product_id.lower()}?hl=en-US&gl=US")
        Gtk.UriLauncher(uri="https://store.rg-adguard.net/").launch(self.get_root(), None, None, None)
        self.toast("Product link copied: paste it on store.rg-adguard.net")

    # ---- action button ----

    def _installed(self) -> tuple[runtimes.Runtime, runtimes.InstalledApp] | None:
        if not self.product:
            return None
        for runtime in runtimes.discover():
            app = runtime.find_app(self.product.family_names[0] if self.product.family_names else "",
                                   self.product.product_id)
            if app:
                return runtime, app
        return None

    def _refresh(self) -> None:
        if not self.product:
            return
        job = self.job
        if job and not job.finished:
            self.button.set_label("Cancel")
            self.button.remove_css_class("suggested-action")
            self.progress.set_visible(True)
            if job.total:
                self.progress.set_fraction(job.fraction)
                self.status.set_label(f"{job.stage}\n{human_size(job.done)} of {human_size(job.total)}"
                                      if job.state == "downloading" else job.stage)
            else:
                self.progress.pulse()
                self.status.set_label(job.stage or "Waiting…")
            return
        self.progress.set_visible(False)
        self.button.add_css_class("suggested-action")
        installed = self._installed()
        outdated = self.offer.outdated() if self.offer else []
        if outdated:
            runtime, app = outdated[0]
            self.button.set_label("Update")
            self.status.set_label(f"{app.version} in {runtime.name}\n{self.offer.version} available")
        elif installed:
            self.button.set_label("Open")
            version = f" ({installed[1].version})" if installed[1].version else ""
            self.status.set_label(f"Installed in {installed[0].name}{version}")
        elif self.product.is_msixvc:
            self.button.set_label("Not supported yet")
            self.button.set_sensitive(False)
            self.status.set_label("Xbox PC (MSIXVC) games need the Xbox app's install path")
        elif not self.product.is_free:
            self.button.set_label(self.product.price)
            self.status.set_label("Paid: buy on Microsoft's site; owned titles need account sign-in")
        else:
            self.button.set_label("Get")
            self.status.set_label("" if not job or job.state != "failed" else f"Last attempt failed: {job.error}")

    def _on_action(self, _button: Gtk.Button) -> None:
        if not self.product:
            return
        if self.job and not self.job.finished:
            installer.get().cancel(self.job)
            return
        outdated = self.offer.outdated() if self.offer else []
        if outdated:
            runtime, _app = outdated[0]
            logo = catalog.image_url(catalog.pick_image(self.product.images, ("logo", "tile", "BoxArt")), 192)
            self.job = installer.get().enqueue(self.product.product_id, self.product.title, logo or "",
                                               self.product.is_game, runtime.id)
            self._refresh()
            return
        installed = self._installed()
        if installed:
            runtime, app = installed
            runtime.run(app)
            self.toast(f"Starting {app.title}")
            return
        if not self.product.is_free:
            Gtk.UriLauncher(uri=f"https://apps.microsoft.com/detail/{self.product.product_id}").launch(
                self.get_root(), None, None, None)
            return
        runtime = runtimes.runtime_for(self.product.is_game)
        if runtime is None or not runtime.ready():
            self.toast("Finish setting up a runtime first")
            self.open_settings()
            return
        logo = catalog.image_url(catalog.pick_image(self.product.images, ("logo", "tile", "BoxArt")), 192)
        self.job = installer.get().enqueue(self.product.product_id, self.product.title, logo or "", self.product.is_game)
        self._refresh()

    def _job_changed(self, job: installer.Job) -> None:
        if job.product_id != self.product_id:
            return
        def apply() -> bool:
            self.job = job
            self._refresh()
            if job.state == "done":
                self.toast(f"{job.title} is installed")
                if self.product and self.product.category_id:
                    self._check_offer()
            elif job.state == "failed":
                self.toast(f"{job.title}: {job.error}")
            return GLib.SOURCE_REMOVE
        GLib.idle_add(apply)


# ---- library -----------------------------------------------------------------------------------

class LibraryPage(Gtk.Box):
    def __init__(self, open_product: OpenProduct, toast: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.open_product = open_product
        self.toast = toast
        installer.get().subscribe(lambda job: job.state == "done" and GLib.idle_add(self.reload))

    def reload(self) -> bool:
        clear(self)
        self.append(widgets.loading("Reading installed apps…"))
        run_async(self._collect, self._show, lambda e: self._show_error(str(e)))
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _collect() -> list[tuple[runtimes.Runtime, list[runtimes.InstalledApp]]]:
        from .. import paths
        out = []
        for runtime in runtimes.discover():
            if runtime.profile_dir or (paths.data_dir() / "profiles" / runtime.id).is_dir():
                apps = runtime.apps()
                if apps:
                    out.append((runtime, apps))
        return out

    def _show_error(self, message: str) -> None:
        clear(self)
        self.append(widgets.error_page(message, self.reload))

    def _show(self, groups) -> None:
        clear(self)
        if not groups:
            self.append(Adw.StatusPage(icon_name="view-grid-symbolic", title="Nothing installed yet",
                                       description="Apps and games you get from the Store show up here", vexpand=True))
            return
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        for side in ("top", "bottom", "start", "end"):
            getattr(body, f"set_margin_{side}")(24)
        for runtime, apps in groups:
            group = Adw.PreferencesGroup(title=runtime.name, description=str(runtime.profile))
            for app in apps:
                group.add(self._app_row(runtime, app))
            body.append(group)
        self.append(widgets.page_scroller(body))

    def _app_row(self, runtime: runtimes.Runtime, app: runtimes.InstalledApp) -> Adw.ActionRow:
        row = Adw.ActionRow(title=app.title, subtitle=app.full_name or app.shortcut, subtitle_lines=1)
        icon = Gtk.Image(pixel_size=40)
        icon.set_from_icon_name("application-x-executable")
        if app.icon_path:
            icon.set_from_file(app.icon_path)
        elif app.logo_url:
            load_image(icon, app.logo_url)
        row.add_prefix(icon)
        if app.product_id:
            row.set_activatable(True)
            row.connect("activated", lambda *_: self.open_product(app.product_id, app.title))
        play = Gtk.Button(label="Stop" if runtime.running(app) else "Play", valign=Gtk.Align.CENTER)
        play.add_css_class("pill")
        if not runtime.running(app):
            play.add_css_class("suggested-action")
        play.connect("clicked", lambda b: self._toggle(runtime, app, b))
        row.add_suffix(play)

        menu = Gio.Menu()
        menu.append("Open log", f"lib.log::{app.stage}")
        menu.append("Uninstall", f"lib.uninstall::{app.stage}")
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER)
        more.add_css_class("flat")
        group = Gio.SimpleActionGroup()
        log_action = Gio.SimpleAction.new("log", GLib.VariantType.new("s"))
        log_action.connect("activate", lambda *_: self._open_log(runtime, app))
        group.add_action(log_action)
        un_action = Gio.SimpleAction.new("uninstall", GLib.VariantType.new("s"))
        un_action.connect("activate", lambda *_: self._uninstall(runtime, app))
        group.add_action(un_action)
        row.insert_action_group("lib", group)
        row.add_suffix(more)
        return row

    def _toggle(self, runtime: runtimes.Runtime, app: runtimes.InstalledApp, button: Gtk.Button) -> None:
        if runtime.running(app):
            run_async(lambda: runtime.stop(app), lambda _r: self.reload())
            button.set_sensitive(False)
            return
        runtime.run(app)
        self.toast(f"Starting {app.title}")
        GLib.timeout_add_seconds(4, self.reload)

    def _open_log(self, runtime: runtimes.Runtime, app: runtimes.InstalledApp) -> None:
        for name in (f"{app.stage}.log", f"{app.stage}.store.log"):
            path = runtime.profile / "logs" / name
            if path.exists():
                Gtk.FileLauncher(file=Gio.File.new_for_path(str(path))).launch(self.get_root(), None, None, None)
                return
        self.toast("No log yet")

    def _uninstall(self, runtime: runtimes.Runtime, app: runtimes.InstalledApp) -> None:
        dialog = Adw.AlertDialog(heading=f"Uninstall {app.title}?",
                                 body="Its files and data in this runtime's profile are deleted.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("uninstall", "Uninstall")
        dialog.set_response_appearance("uninstall", Adw.ResponseAppearance.DESTRUCTIVE)

        def response(_d, choice: str) -> None:
            if choice == "uninstall":
                run_async(lambda: runtime.uninstall(app), lambda _r: (self.toast(f"{app.title} uninstalled"), self.reload()),
                          lambda e: self.toast(str(e)))
        dialog.connect("response", response)
        dialog.present(self.get_root())
