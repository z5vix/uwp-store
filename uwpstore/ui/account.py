"""Microsoft account: the header button and the device-code sign-in dialog."""

from __future__ import annotations

import threading
from typing import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from .. import account
from .util import run_async


class SignInDialog(Adw.Dialog):
    """Shows the one-time code and waits while the user approves it on microsoft.com/link in their browser."""

    def __init__(self, on_done: Callable[[object], None], client_id: str = account.CLIENT_ID,
                 heading: str = "Sign in with Microsoft", intro: str = "",
                 finish: Callable[[dict], object] | None = None) -> None:
        """The store's own sign-in by default. With client_id and finish: a sign-in for another Microsoft client (a
        game's own), whose token response goes to finish (run off the main thread) instead of the account list."""
        super().__init__(title=heading, content_width=440)
        self.on_done = on_done
        self.client_id = client_id
        self.finish = finish
        self._cancel = threading.Event()
        self.code: account.DeviceCode | None = None

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12, margin_bottom=24,
                      margin_start=24, margin_end=24)
        intro = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                          label=intro or "Your browser opens Microsoft's sign-in page with this code filled in. Sign "
                                         "in there and approve; your password never passes through this app.")
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
        run_async(lambda: account.start_device_code(client_id), self._got_code, self._failed)

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
        if self.finish is None:
            run_async(lambda: account.poll_device_code(code, self._cancel.is_set), self._signed_in, self._failed)
        else:
            run_async(lambda: self.finish(account.poll_client_code(code, self.client_id, self._cancel.is_set)),
                      self._signed_in, self._failed)

    def _signed_in(self, result: object) -> None:
        self.on_done(result)
        self.close()

    def _failed(self, error: Exception) -> None:
        if self._cancel.is_set():
            return
        self.spinner.set_visible(False)
        self.status.set_label(str(error))


class AccountButton(Gtk.MenuButton):
    """The header's account menu: every signed-in account (pick the active one), add another, sign out."""

    def __init__(self, toast: Callable[[str], None]) -> None:
        super().__init__(icon_name="avatar-default-symbolic", tooltip_text="Microsoft accounts")
        self.toast = toast
        group = Gio.SimpleActionGroup()
        add = Gio.SimpleAction.new("signin", None)
        add.connect("activate", lambda *_a: self.sign_in())
        group.add_action(add)
        self.switch_action = Gio.SimpleAction.new_stateful("switch", GLib.VariantType.new("s"), GLib.Variant("s", ""))
        self.switch_action.connect("activate", lambda _a, v: self.switch(v.get_string()))
        group.add_action(self.switch_action)
        out = Gio.SimpleAction.new("signout", GLib.VariantType.new("s"))
        out.connect("activate", lambda _a, v: self.sign_out(v.get_string()))
        group.add_action(out)
        self.insert_action_group("account", group)
        self.refresh()
        missing = [a for a in account.accounts() if not a.gamertag]
        for profile in missing:
            run_async(lambda p=profile: account.profile(refresh=True, account_id=p.id), lambda _p: self.refresh(),
                      lambda _e: None)

    def refresh(self) -> None:
        found = account.accounts()
        current = account.active()
        menu = Gio.Menu()
        if found:
            people = Gio.Menu()
            for profile in found:
                people.append(profile.name, f"account.switch::{profile.id}")
            menu.append_section("Accounts" if len(found) > 1 else None, people)
            actions = Gio.Menu()
            actions.append("Add another account…", "account.signin")
            if current:
                actions.append(f"Sign out {current.name}", f"account.signout::{current.id}")
            menu.append_section(None, actions)
            self.switch_action.set_state(GLib.Variant("s", current.id if current else ""))
            self.set_tooltip_text(f"Signed in as {current.name}" + (f" (+{len(found) - 1} more)" if len(found) > 1 else ""))
            self.add_css_class("accent")
        else:
            menu.append("Sign in with Microsoft…", "account.signin")
            self.set_tooltip_text("Sign in with a Microsoft account")
            self.remove_css_class("accent")
        self.set_menu_model(menu)

    def sign_in(self) -> None:
        def done(profile: account.Profile) -> None:
            self.refresh()
            self.toast(f"Signed in as {profile.name}")
        SignInDialog(done).present(self.get_root())

    def switch(self, account_id: str) -> None:
        account.set_active(account_id)
        self.refresh()
        current = account.active()
        if current:
            self.toast(f"Active account: {current.name}")

    def sign_out(self, account_id: str) -> None:
        who = account.get(account_id)
        account.sign_out(account_id)
        if who and who.xuid:
            from .. import xboxaccounts
            xboxaccounts.forget_everywhere(who.xuid)
        self.refresh()
        self.toast(f"Signed out {who.name if who else ''}".strip())


def status_text() -> str:
    found = account.accounts()
    if not found:
        return "Not signed in. Free apps and games install without an account."
    current = account.active()
    others = len(found) - 1
    return f"Active: {current.name}" + (f", and {others} more signed in" if others else "")


def start_app(parent: Gtk.Widget, runtime, app, toast: Callable[[str], None],
              started: Callable[[], None] | None = None) -> None:
    """Start an installed app. A GDK game that signs in as its own Microsoft client gets that one-time sign-in first."""
    from .. import gdk

    def run(_result: object = None) -> None:
        runtime.run(app)
        toast(f"Starting {app.title}")
        if started:
            started()

    def checked(client: str) -> None:
        if not client:
            run()
            return
        who = gdk.signed_in_account(runtime)
        intro = (f"{app.title} signs in to Xbox Live as its own Microsoft app, which needs your approval once. Your "
                 f"browser opens Microsoft's page with this code filled in: approve it as "
                 f"{who.name if who else 'the account that owns the game'}. Your password never passes through "
                 "this app.")
        SignInDialog(run, client_id=client, heading=f"Sign in for {app.title}", intro=intro,
                     finish=lambda grant: gdk.import_title_signin(runtime, client, grant)).present(parent)

    def prepare() -> str:
        """Off the main thread: what an installed GDK game still needs before it starts."""
        if gdk.redistributables_pending(runtime, app.stage):
            gdk.install_redistributables(runtime, app.stage)
        return gdk.title_signin_needed(runtime, app) if gdk.title_client(runtime, app) else ""

    if not (runtime.stage_dir / app.stage / gdk.MARKER).is_file():
        run()
        return
    run_async(prepare, checked, lambda _e: checked(""))
