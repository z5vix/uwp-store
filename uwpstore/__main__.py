"""uwp-store: GUI by default; a few command-line verbs for scripting and debugging."""

from __future__ import annotations

import argparse
import sys


def cli(argv: list[str]) -> int:
    from . import account, catalog, fe3, installer, runtimes

    parser = argparse.ArgumentParser(prog="uwp-store")
    sub = parser.add_subparsers(dest="cmd")
    s = sub.add_parser("search", help="search the catalogue")
    s.add_argument("query")
    p = sub.add_parser("product", help="show a product")
    p.add_argument("product_id")
    k = sub.add_parser("packages", help="list a product's packages from the delivery service")
    k.add_argument("product_id")
    i = sub.add_parser("install", help="install a free product")
    i.add_argument("product_id")
    i.add_argument("--runtime", default="", help="runtime id (default: the app or game runtime from Settings)")
    sub.add_parser("store", help="Microsoft Store app: newest version offered vs installed")
    sub.add_parser("signin", help="sign in (or add another) Microsoft account (device code in your browser)")
    so = sub.add_parser("signout", help="forget a Microsoft account (default: the active one)")
    so.add_argument("account", nargs="?", default="", help="gamertag or xuid")
    sub.add_parser("account", help="list signed-in accounts")
    sw = sub.add_parser("switch", help="make another signed-in account the active one")
    sw.add_argument("account", help="gamertag or xuid")
    ow = sub.add_parser("owners", help="which signed-in accounts own a product")
    ow.add_argument("product_id")
    xb = sub.add_parser("xbox-account", help="show or set which account the Xbox app uses in each runtime")
    xb.add_argument("account", nargs="?", default="", help="gamertag or xuid (omit to show)")
    xb.add_argument("--runtime", default="")
    sub.add_parser("runtimes", help="list runtimes")
    sub.add_parser("apps", help="list installed apps")
    r = sub.add_parser("run", help="run an installed app (stage name or product id)")
    r.add_argument("app")
    r.add_argument("--runtime", default="", help="only look in this runtime")
    sub.add_parser("menu", help="add installed apps to the start menu (and remove stale entries)")
    args = parser.parse_args(argv)

    if args.cmd == "search":
        for card in catalog.search(args.query):
            print(f"{card.product_id}  {card.price:>10}  {card.title}")
    elif args.cmd == "product":
        prod = catalog.product(args.product_id)
        print(f"{prod.title} — {prod.publisher}\n{prod.price} · {prod.category}\ncategory id: {prod.category_id}\n"
              f"installer: {prod.installer}\nfamilies: {', '.join(prod.family_names)}")
    elif args.cmd == "packages":
        prod = catalog.product(args.product_id)
        res = fe3.DeliveryClient().resolve(prod.category_id)
        for f in res.files:
            print(f"{'F' if f.is_framework else ' '}{'E' if f.is_encrypted else ' '} {f.size:>12}  {f.moniker}.{f.extension}")
        if res.needs_license:
            print("(some packages need a licence)")
    elif args.cmd == "install":
        prod = catalog.product(args.product_id)
        inst = installer.get()
        done = __import__("threading").Event()

        def show(job: installer.Job) -> None:
            pct = f" {job.fraction * 100:5.1f}%" if job.total else ""
            print(f"\r{job.state:12} {job.stage[:60]:60}{pct}", end="", flush=True)
            if job.finished:
                print(f"\n{job.state}{': ' + job.error if job.error else ''}")
                done.set()
        inst.subscribe(show)
        inst.enqueue(prod.product_id, prod.title, "", prod.is_game, args.runtime)
        done.wait()
    elif args.cmd == "store":
        prod = catalog.product(installer.STORE_PRODUCT_ID)
        offer = installer.offer(prod)
        print(f"latest  {offer.package.moniker}.{offer.package.extension}  {offer.package.size} bytes")
        outdated = {rt.id for rt, _ in offer.outdated()}
        for rt, app in offer.installed:
            print(f"{rt.id:24} {app.version:18} {'update available' if rt.id in outdated else 'up to date'}")
        if outdated:
            print("update with: uwp-store install " + installer.STORE_PRODUCT_ID + " --runtime RUNTIME")
    elif args.cmd == "signin":
        import webbrowser
        code = account.start_device_code()
        print(f"Open {code.link}\nand approve the code {code.user_code} (waiting…)", flush=True)
        webbrowser.open(code.link)
        profile = account.poll_device_code(code)
        print(f"Signed in as {profile.gamertag or profile.xuid or 'your Microsoft account'}")
    elif args.cmd in ("signout", "switch", "xbox-account") and args.account and not _find_account(account, args.account):
        print(f"no signed-in account {args.account}", file=sys.stderr)
        return 1
    elif args.cmd == "signout":
        from . import xboxaccounts
        target = _find_account(account, args.account) if args.account else account.active()
        if target is None:
            print("Not signed in")
            return 1
        account.sign_out(target.id)
        if target.xuid:
            xboxaccounts.forget_everywhere(target.xuid)
        print(f"Signed out {target.name}")
    elif args.cmd == "switch":
        target = _find_account(account, args.account)
        account.set_active(target.id)
        print(f"Active account: {target.name}")
    elif args.cmd == "account":
        current = account.active()
        if not current:
            print("Not signed in")
        for prof in account.accounts():
            ticket = "delivery ticket ok" if account.store_ticket(prof.id) else "delivery ticket refused"
            print(f"{'*' if prof.id == current.id else ' '} {prof.name:20} xuid {prof.xuid:18} {ticket}")
    elif args.cmd == "owners":
        found = account.owners(args.product_id)
        print(", ".join(p.name for p in found) if found else "none of the signed-in accounts")
    elif args.cmd == "xbox-account":
        from . import xboxaccounts
        for rt in xboxaccounts.runtimes_with_xbox_app():
            if args.runtime and rt.id != args.runtime:
                continue
            if args.account:
                target = _find_account(account, args.account)
                xboxaccounts.switch(rt, target.xuid)
            now = xboxaccounts.signed_in_as(rt)
            who = account.get(now)
            print(f"{rt.id:24} {who.name if who else (now or 'signed out')}")
    elif args.cmd == "runtimes":
        for rt in runtimes.discover():
            print(f"{rt.id:28} {rt.flavour:11} {'ready' if rt.ready() else 'not ready':10} {rt.name}")
    elif args.cmd == "apps":
        for app in runtimes.all_apps():
            print(f"{app.runtime_id:24} {app.stage:28} {app.title}")
    elif args.cmd == "menu":
        from . import desktop
        print(f"{desktop.sync()} start-menu entries")
    elif args.cmd == "run":
        for rt in runtimes.discover():
            if args.runtime and rt.id != args.runtime:
                continue
            for app in rt.apps():
                if args.app in (app.stage, app.product_id):
                    rt.run(app)
                    return 0
        print("no such app", file=sys.stderr)
        return 1
    else:
        parser.print_help()
    return 0


def _find_account(account, name: str):
    name = name.lower()
    return next((a for a in account.accounts() if name in (a.gamertag.lower(), a.xuid, a.id.lower())), None)


def main() -> int:
    start_page, open_settings, product, query, sign_in = None, False, None, None, False
    args = sys.argv[1:]
    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag.startswith("--page="):
            start_page = flag.split("=", 1)[1]
        elif flag == "--settings":
            open_settings = True
        elif flag == "--sign-in":
            sign_in = True
        elif flag.startswith("--product="):
            product = flag.split("=", 1)[1]
        elif flag.startswith("--search="):
            query = flag.split("=", 1)[1]
        else:
            print(f"unknown option {flag}", file=sys.stderr)
            return 2
    if args:
        return cli(args)
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio

    from .paths import APP_ID
    from .ui.window import StoreWindow

    app = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    from gi.repository import Gtk
    Gtk.Window.set_default_icon_name(APP_ID)   # installed by install-desktop.sh

    def activate(application: Adw.Application) -> None:
        window = application.get_active_window() or StoreWindow(application)
        window.present()
        if start_page:
            window.stack.set_visible_child_name(start_page)
        if product:
            window.open_product(product, "")
        if query:
            window.search_entry.set_text(query)
            window.search_button.set_active(True)
            window._search(query)
        if open_settings:
            window.open_settings()
        if sign_in:
            window.account_button.sign_in()
    app.connect("activate", activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
