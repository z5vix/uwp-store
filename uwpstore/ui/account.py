"""Microsoft account: the header button and the device-code sign-in dialog."""

from __future__ import annotations

import threading
from typing import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from .. import account
from .util import run_async


class SignInDialog(Adw.Dialog):
    """Shows the one-time code and waits while the user approves it on microsoft.com/link in their browser."""

    def __init__(self, on_done: Callable[[account.Profile], None]) -> None:
        super().__init__(title="Sign in with Microsoft", content_width=440)
        self.on_done = on_done
        self._cancel = threading.Event()
        self.code: account.DeviceCode | None = None

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12, margin_bottom=24,
                      margin_start=24, margin_end=24)
        intro = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                          label="Your browser opens Microsoft's sign-in page with this code filled in. Sign in there "
                                "and approve; your password never passes through this app.")
        box.append(intro)
        self.code_label = Gtk.Label(label="…", selectable=True)
        self.code_label.add_css_class("title-1")
        self.code_label.add_css_class("monospace")
        box.append(self.code_label)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.open_button = Gtk.Button(label="Open sign-in page", sensitive=False)
        self.open_button.add_css_class("pill")
        self.open_button.add_css_class("suggested-action")
        self.open_button.connect("clicked", lambda *_: self._open())
        buttons.append(self.open_button)
        self.copy_button = Gtk.Button(label="Copy code", sensitive=False)
        self.copy_button.add_css_class("pill")
        self.copy_button.connect("clicked", lambda *_: self.code and self.get_clipboard().set(self.code.user_code))
        buttons.append(self.copy_button)
        box.append(buttons)
        status_box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.spinner = Adw.Spinner()
        status_box.append(self.spinner)
        self.status = Gtk.Label(label="Asking Microsoft for a code…", wrap=True)
        self.status.add_css_class("dim-label")
        status_box.append(self.status)
        box.append(status_box)
        view.set_content(box)
        self.set_child(view)
        self.connect("closed", lambda *_: self._cancel.set())
        run_async(account.start_device_code, self._got_code, self._failed)

    def _open(self) -> None:
        if self.code:
            Gtk.UriLauncher(uri=self.code.link).launch(self.get_root(), None, None, None)

    def _got_code(self, code: account.DeviceCode) -> None:
        self.code = code
        self.code_label.set_label(code.user_code)
        self.open_button.set_sensitive(True)
        self.copy_button.set_sensitive(True)
        self.status.set_label("Waiting for you to approve in the browser…")
        self._open()
        run_async(lambda: account.poll_device_code(code, self._cancel.is_set), self._signed_in, self._failed)

    def _signed_in(self, profile: account.Profile) -> None:
        self.on_done(profile)
        self.close()

    def _failed(self, error: Exception) -> None:
        if self._cancel.is_set():
            return
        self.spinner.set_visible(False)
        self.status.set_label(str(error))


class AccountButton(Gtk.MenuButton):
    """The header's account menu: sign in, or the gamertag and sign out."""

    def __init__(self, toast: Callable[[str], None]) -> None:
        super().__init__(icon_name="avatar-default-symbolic", tooltip_text="Microsoft account")
        self.toast = toast
        self.listeners: list[Callable[[], None]] = []
        group = Gio.SimpleActionGroup()
        for name, handler in (("signin", self.sign_in), ("signout", self.sign_out)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, h=handler: h())
            group.add_action(action)
        self.insert_action_group("account", group)
        self.refresh()
        if account.signed_in() and not account.cached_profile():
            run_async(account.profile, lambda _p: self.refresh(), lambda _e: None)

    def refresh(self) -> None:
        menu = Gio.Menu()
        profile = account.cached_profile()
        if account.signed_in():
            name = profile.gamertag if profile and profile.gamertag else "Microsoft account"
            self.set_tooltip_text(f"Signed in as {name}")
            section = Gio.Menu()
            section.append(f"Signed in as {name}", "account.none")
            menu.append_section(None, section)
            menu.append("Sign out", "account.signout")
            self.set_icon_name("avatar-default-symbolic")
            self.add_css_class("accent")
        else:
            self.set_tooltip_text("Sign in with a Microsoft account")
            menu.append("Sign in with Microsoft…", "account.signin")
            self.remove_css_class("accent")
        self.set_menu_model(menu)
        for listener in list(self.listeners):
            listener()

    def sign_in(self) -> None:
        def done(profile: account.Profile) -> None:
            self.refresh()
            self.toast(f"Signed in as {profile.gamertag or 'your Microsoft account'}")
        SignInDialog(done).present(self.get_root())

    def sign_out(self) -> None:
        account.sign_out()
        self.refresh()
        self.toast("Signed out")


def status_text() -> str:
    if not account.signed_in():
        return "Not signed in. Free apps and games install without an account."
    profile = account.cached_profile()
    who = profile.gamertag if profile and profile.gamertag else "a Microsoft account"
    return f"Signed in as {who}"


def idle(fn: Callable[[], None]) -> None:
    GLib.idle_add(lambda: (fn(), GLib.SOURCE_REMOVE)[1])
