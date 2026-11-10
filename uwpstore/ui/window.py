"""Main window: Home / Apps / Gaming / Library, search, downloads, and pushed product pages."""

from __future__ import annotations

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .. import catalog, installer, runtimes
from . import pages, widgets
from .account import AccountButton
from .setup import SettingsDialog
from .util import clear, human_size, load_image

CSS = b"""
.store-card { padding: 12px; border-radius: 14px; }
.store-art { border-radius: 10px; }
.store-hero { border-radius: 18px; }
.store-hero picture { border-radius: 18px; }
.store-hero-shade {
  padding: 28px; border-radius: 0 0 18px 18px;
  background: linear-gradient(to top, alpha(black, 0.78), alpha(black, 0.0));
  color: white;
}
.store-shot { border-radius: 12px; }
.store-row scrollbar { opacity: 0.4; }
"""


class DownloadsButton(Gtk.MenuButton):
    def __init__(self) -> None:
        super().__init__(icon_name="folder-download-symbolic", tooltip_text="Downloads")
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.empty = Gtk.Label(label="No downloads", margin_top=24, margin_bottom=24)
        self.empty.add_css_class("dim-label")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=380)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(8)
        box.append(self.empty)
        scroller = Gtk.ScrolledWindow(child=self.list, propagate_natural_height=True, max_content_height=480)
        box.append(scroller)
        self.set_popover(Gtk.Popover(child=box))
        self.rows: dict[int, tuple[Adw.ActionRow, Gtk.ProgressBar, Gtk.Button]] = {}
        installer.get().subscribe(lambda job: GLib.idle_add(self._update, job))

    def _update(self, job: installer.Job) -> bool:
        key = id(job)
        if key not in self.rows:
            row = Adw.ActionRow(title=job.title)
            icon = Gtk.Image(pixel_size=32, icon_name="application-x-executable")
            load_image(icon, job.logo_url)
            row.add_prefix(icon)
            bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, width_request=90)
            row.add_suffix(bar)
            cancel = Gtk.Button(icon_name="process-stop-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Cancel")
            cancel.add_css_class("flat")
            cancel.connect("clicked", lambda *_: installer.get().cancel(job))
            row.add_suffix(cancel)
            self.list.prepend(row)
            self.rows[key] = (row, bar, cancel)
        row, bar, cancel = self.rows[key]
        self.empty.set_visible(False)
        if job.state == "downloading" and job.total:
            row.set_subtitle(f"{job.stage} · {human_size(job.done)} of {human_size(job.total)}")
        elif job.state == "failed":
            row.set_subtitle(f"Failed: {job.error}")
        else:
            row.set_subtitle(job.stage or job.state.capitalize())
        bar.set_visible(not job.finished)
        cancel.set_visible(not job.finished)
        if job.total:
            bar.set_fraction(job.fraction)
        else:
            bar.pulse()
        active = any(not j.finished for j in installer.get().jobs)
        if active:
            self.add_css_class("accent")
        else:
            self.remove_css_class("accent")
        return GLib.SOURCE_REMOVE


class StoreWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="UWP Store", default_width=1280, default_height=860)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.toasts = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        self.toasts.set_child(self.nav)
        self.set_content(self.toasts)

        # root page
        root_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)

        self.search_button = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search")
        header.pack_start(self.search_button)
        header.pack_end(self._menu_button())
        self.account_button = AccountButton(self.toast)
        header.pack_end(self.account_button)
        header.pack_end(DownloadsButton())
        root_view.add_top_bar(header)

        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search apps and games", width_chars=40)
        self.search_bar.set_child(self.search_entry)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(self)
        self.search_button.bind_property("active", self.search_bar, "search-mode-enabled",
                                         GObject_flags())
        self.search_entry.connect("activate", lambda e: self._search(e.get_text()))
        self.search_entry.connect("search-changed", self._search_changed)
        root_view.add_top_bar(self.search_bar)

        self.banner = Adw.Banner(button_label="Set up")
        self.banner.connect("button-clicked", lambda *_: self.open_settings())
        root_view.add_top_bar(self.banner)

        self.home = pages.BrowsePage("home", self.open_product, self.open_list)
        self.apps = pages.BrowsePage("apps", self.open_product, self.open_list)
        self.gaming = pages.BrowsePage("gaming", self.open_product, self.open_list)
        self.library = pages.LibraryPage(self.open_product, self.toast)
        self.search_page = pages.SearchPage(self.open_product)
        self.stack.add_titled_with_icon(self.home, "home", "Home", "go-home-symbolic")
        self.stack.add_titled_with_icon(self.apps, "apps", "Apps", "view-app-grid-symbolic")
        self.stack.add_titled_with_icon(self.gaming, "gaming", "Gaming", "input-gaming-symbolic")
        self.stack.add_titled_with_icon(self.library, "library", "Library", "view-grid-symbolic")
        self.stack.add_named(self.search_page, "search")
        self.stack.connect("notify::visible-child", self._page_changed)
        root_view.set_content(self.stack)

        bottom = Adw.ViewSwitcherBar(stack=self.stack)
        root_view.add_bottom_bar(bottom)
        breakpoint_ = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        breakpoint_.add_setter(switcher, "visible", False)
        breakpoint_.add_setter(bottom, "reveal", True)
        self.add_breakpoint(breakpoint_)

        self.nav.add(Adw.NavigationPage(child=root_view, title="Store", tag="root"))
        self.home.ensure_loaded()
        self._update_banner()

        actions = (("settings", lambda *_: self.open_settings()), ("refresh", lambda *_: self._refresh()),
                   ("about", lambda *_: self._about()))
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        app.set_accels_for_action("win.refresh", ["F5", "<Control>r"])
        app.set_accels_for_action("win.settings", ["<Control>comma"])

    # ---- navigation ----

    def _menu_button(self) -> Gtk.MenuButton:
        menu = Gio.Menu()
        menu.append("Settings", "win.settings")
        menu.append("Refresh", "win.refresh")
        menu.append("About", "win.about")
        return Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True)

    def _page_changed(self, *_args) -> None:
        name = self.stack.get_visible_child_name()
        if name == "library":
            self.library.reload()
        elif name in ("home", "apps", "gaming"):
            getattr(self, name).ensure_loaded()

    def open_product(self, product_id: str, title: str) -> None:
        if not product_id:
            return
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(pages.ProductPage(product_id, self.toast, self.open_settings))
        self.nav.push(Adw.NavigationPage(child=view, title=title or "Product"))

    def open_list(self, title: str, section: catalog.Section) -> None:
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(pages.ListPage(section.cards, self.open_product))
        self.nav.push(Adw.NavigationPage(child=view, title=title))

    def _search(self, text: str) -> None:
        if text.strip():
            self.nav.pop_to_tag("root")
            self.stack.set_visible_child_name("search")
            self.search_page.search(text)

    def _search_changed(self, entry: Gtk.SearchEntry) -> None:
        if len(entry.get_text().strip()) >= 3:
            self._search(entry.get_text())

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def open_settings(self) -> None:
        SettingsDialog(self.toast, self._settings_changed).present(self)

    def _settings_changed(self) -> None:
        self._update_banner()
        self.account_button.refresh()

    def _update_banner(self) -> None:
        found = runtimes.discover()
        if not found:
            self.banner.set_title("No UWP runtime yet: build GE-Proton UWP or wine-uwp to install apps")
            self.banner.set_revealed(True)
        elif runtimes.shared_windows_tree() is None:
            self.banner.set_title("Choose your Windows 11 ISO so apps can get Microsoft's frameworks")
            self.banner.set_revealed(True)
        elif not any(r.prefix_ready() for r in found):
            self.banner.set_title("Create a prefix for a runtime to start installing")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    def _refresh(self) -> None:
        from .. import net
        net.clear_cache()
        for page in (self.home, self.apps, self.gaming):
            if page.loaded:
                page.reload()
        self.toast("Refreshed")

    def _about(self) -> None:
        about = Adw.AboutDialog(application_name="UWP Store", version="2.0", developer_name="UWP Store contributors",
                                comments="A native Linux storefront for Microsoft Store apps and games, "
                                         "running them with wine-uwp and GE-Proton UWP.",
                                license_type=Gtk.License.LGPL_2_1)
        about.present(self)


def GObject_flags():
    from gi.repository import GObject
    return GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE
