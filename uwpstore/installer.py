"""Install jobs: product -> packages -> downloads -> frameworks -> runtime install.

A Job runs on a worker thread and reports through a listener callback; the UI marshals those calls
onto the main loop. Jobs are queued and run one at a time so two installs never share a prefix.
"""

from __future__ import annotations

import hashlib
import queue
import re
import shlex
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import account, catalog, fe3, net, packages, paths, runtimes, settings


class InstallError(Exception):
    pass


@dataclass
class Job:
    product_id: str
    title: str
    logo_url: str
    is_game: bool
    runtime_id: str = ""
    account_id: str = ""          # the signed-in account whose licence the download uses (the owner)
    state: str = "queued"         # queued, resolving, downloading, installing, done, failed, cancelled
    stage: str = ""
    done: int = 0
    total: int = 0
    error: str = ""
    log: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    started: float = field(default_factory=time.time)

    @property
    def fraction(self) -> float:
        return min(1.0, self.done / self.total) if self.total else 0.0

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed", "cancelled")


Listener = Callable[[Job], None]


def choose_main_package(resolution: fe3.Resolution, family_name: str) -> fe3.PackageFile:
    """The newest installable (unencrypted, x64/neutral/x86) app package or bundle of the product."""
    family = family_name.split("_")[0].lower() if family_name else ""
    candidates = [p for p in resolution.files if not p.is_framework]
    if family:
        named = [p for p in candidates if p.name.lower() == family]
        candidates = named or candidates
    if not candidates:
        if resolution.needs_license:
            raise InstallError("This product needs a licence from a signed-in Microsoft account to download.")
        raise InstallError("The Store offers no package for this product.")
    plain = [p for p in candidates if not p.is_encrypted and p.extension not in ("appxvc", "msixvc")]
    if not plain:
        raise InstallError("This product only ships encrypted (DRM-protected) packages, which this store cannot install.")
    usable = [p for p in plain if p.arch in packages.ARCH_PREFERENCE]
    if not usable:
        raise InstallError("This product has no x64, x86 or neutral package.")

    def rank(p: fe3.PackageFile):
        return (packages.version_tuple(p.version), p.is_bundle, -packages.ARCH_PREFERENCE.index(p.arch))
    return max(usable, key=rank)


def choose_framework(resolution: fe3.Resolution, dep: packages.Dependency, arch: str = "x64") -> fe3.PackageFile | None:
    matches = [p for p in resolution.files if p.name.lower() == dep.name.lower() and p.arch == arch
               and not p.is_encrypted and packages.version_tuple(p.version) >= packages.version_tuple(dep.min_version)]
    return max(matches, key=lambda p: packages.version_tuple(p.version)) if matches else None


STORE_PRODUCT_ID = "9WZDNCRFJBMP"      # Microsoft Store itself (apps.microsoft.com/detail/9wzdncrfjbmp)
XBOX_APP_PRODUCT_ID = "9MV0B5HZVK9Z"   # the Xbox app, which installs Xbox PC (GDK) games


@dataclass
class Offer:
    """What the delivery service currently offers for a product, against what is installed."""
    package: fe3.PackageFile
    installed: list[tuple[runtimes.Runtime, runtimes.InstalledApp]]

    @property
    def version(self) -> str:
        return self.package.version

    def outdated(self) -> list[tuple[runtimes.Runtime, runtimes.InstalledApp]]:
        newest = packages.version_tuple(self.package.version)
        return [(rt, app) for rt, app in self.installed
                if app.version and packages.version_tuple(app.version) < newest]


def offer(product: catalog.Product, resolution: fe3.Resolution | None = None) -> Offer:
    """The newest installable package of a product (the same files store.rg-adguard.net lists) and its installs."""
    if resolution is None:
        resolution = fe3.DeliveryClient(account.store_ticket()).resolve(product.category_id)
    family = next((s.family_name for s in product.skus if s.family_name), product.family_names[0] if product.family_names else "")
    main = choose_main_package(resolution, family)
    installed = []
    for runtime in runtimes.discover():
        app = runtime.find_app(family, product.product_id)
        if app:
            installed.append((runtime, app))
    return Offer(main, installed)


class Installer:
    def __init__(self) -> None:
        self.jobs: list[Job] = []
        self._queue: "queue.Queue[Job]" = queue.Queue()
        self._listeners: list[Listener] = []
        self._worker = threading.Thread(target=self._run, name="installer", daemon=True)
        self._worker.start()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Add a listener; returns a function that removes it again."""
        self._listeners.append(listener)
        return lambda: listener in self._listeners and self._listeners.remove(listener)

    def _emit(self, job: Job) -> None:
        for listener in list(self._listeners):
            try:
                listener(job)
            except Exception:  # a broken listener must not kill the worker
                traceback.print_exc()

    def active_for(self, product_id: str) -> Job | None:
        for job in self.jobs:
            if job.product_id == product_id and not job.finished:
                return job
        return None

    def enqueue(self, product_id: str, title: str, logo_url: str, is_game: bool, runtime_id: str = "",
                account_id: str = "") -> Job:
        """Queue an install; runtime_id picks the runtime (an update goes where the app already is)."""
        existing = self.active_for(product_id)
        if existing:
            return existing
        job = Job(product_id, title, logo_url, is_game, runtime_id, account_id)
        self.jobs.append(job)
        self._queue.put(job)
        self._emit(job)
        return job

    def cancel(self, job: Job) -> None:
        job.cancel_requested = True
        if job.state == "queued":
            job.state = "cancelled"
        self._emit(job)

    # ---- worker ----

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job.cancel_requested:
                continue
            try:
                self._install(job)
                job.state = "done"
                job.stage = "Installed"
                try:
                    from . import desktop
                    desktop.sync()
                except OSError as exc:      # a menu entry is a convenience; the install itself succeeded
                    job.log.append(f"start-menu entry: {exc}")
            except (InstallError, fe3.DeliveryError, net.NetError, packages.PackageError,
                    runtimes.RuntimeError_, OSError) as exc:
                job.state = "cancelled" if job.cancel_requested else "failed"
                job.error = str(exc)
            except Exception as exc:  # report, never crash the queue
                job.state = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.log.append(traceback.format_exc())
            self._emit(job)

    def _step(self, job: Job, state: str, stage: str, done: int = 0, total: int = 0) -> None:
        if job.cancel_requested:
            raise InstallError("Cancelled")
        job.state, job.stage, job.done, job.total = state, stage, done, total
        self._emit(job)

    def _download(self, job: Job, client: fe3.DeliveryClient, package: fe3.PackageFile, label: str) -> Path:
        target = paths.downloads_dir() / package.local_name
        if target.exists() and target.stat().st_size == package.size:
            if not package.digest or net.verify_digest(target, package.digest, package.digest_alg):
                return target
            target.unlink()
        self._step(job, "downloading", f"Downloading {label}", 0, package.size)
        url = client.url_for(package)

        def progress(done: int, total: int) -> None:
            job.done, job.total = done, total or package.size
            self._emit(job)
        net.download(url, target, expected_size=package.size, progress=progress, cancelled=lambda: job.cancel_requested)
        self._step(job, "downloading", f"Verifying {label}")
        if package.digest and not net.verify_digest(target, package.digest, package.digest_alg):
            target.unlink(missing_ok=True)
            raise InstallError(f"{label}: checksum mismatch; the download was discarded")
        return target

    def _install(self, job: Job) -> None:
        self._step(job, "resolving", "Looking up the product")
        product = catalog.product(job.product_id)
        runtime = next((r for r in runtimes.discover() if r.id == job.runtime_id), None) if job.runtime_id \
            else runtimes.runtime_for(product.is_game)
        if runtime is None:
            raise InstallError("No UWP runtime is set up. Open Settings and build or choose a runtime.")
        job.runtime_id = runtime.id
        if product.installer.lower() not in ("windowsupdate", ""):
            self._install_win32(job, product, runtime)
            return
        if not product.category_id:
            raise InstallError("The Store lists no package for this product.")
        if not runtime.ready():
            raise InstallError(f"{runtime.name} is not set up yet: finish setup in Settings (Windows 11 ISO and prefix).")

        self._step(job, "resolving", "Finding packages")
        client = fe3.DeliveryClient(account.store_ticket(job.account_id or None))
        resolution = client.resolve(product.category_id)
        family = next((s.family_name for s in product.skus if s.family_name), product.family_names[0] if product.family_names else "")
        main = choose_main_package(resolution, family)
        job.log.append(f"package {main.moniker} ({main.size} bytes)")
        main_path = self._download(job, client, main, product.title)

        # frameworks the package names that neither the Windows files nor earlier installs provide
        manifest = packages.read_manifest(main_path)
        framework_root = runtime.stage_dir / "frameworks"
        framework_root.mkdir(parents=True, exist_ok=True)
        windows_apps = runtime.profile / "windows" / "Program Files" / "WindowsApps"
        for dep in manifest.dependencies:
            if packages.installed_framework([framework_root, windows_apps], dep):
                continue
            candidate = choose_framework(resolution, dep)
            if candidate is None:
                # the runtime accepts an older version of a framework (same major ABI); with none at all it cannot install
                older = packages.Dependency(dep.name, "0.0.0.0", dep.publisher)
                if packages.installed_framework([framework_root, windows_apps], older):
                    job.log.append(f"framework {dep.name} >= {dep.min_version}: using an older version")
                    continue
                main_path.unlink(missing_ok=True)
                raise InstallError(f"Needs the {dep.name} framework, which neither your Windows files nor the Store provide.")
            path = self._download(job, client, candidate, candidate.name)
            self._step(job, "installing", f"Unpacking {candidate.name}")
            packages.extract_package(path, framework_root / candidate.moniker)
            if not settings.load().get("keep_downloads"):
                path.unlink(missing_ok=True)

        self._step(job, "installing", "Installing")
        stage_before = {a.stage for a in runtime.apps()}
        for line in runtime.install(main_path, cancelled=lambda: job.cancel_requested):
            job.log.append(line)
            if line.startswith("STAGE "):
                job.stage = line[6:]
            elif line.startswith("PROGRESS "):
                parts = line.split()
                if len(parts) == 3:
                    job.done, job.total = int(parts[1]), int(parts[2])
            self._emit(job)
        installed = [a for a in runtime.apps() if a.stage not in stage_before] or \
            [a for a in runtime.apps() if a.family_name.lower() == f"{manifest.name}_{main.publisher_id}".lower()]
        for app in installed:
            runtime.remember(app.stage, product_id=product.product_id, title=product.title, logo_url=job.logo_url)
        if not settings.load().get("keep_downloads"):
            main_path.unlink(missing_ok=True)

    def _install_win32(self, job: Job, product: catalog.Product, runtime: runtimes.Runtime) -> None:
        """A classic installer from the publisher, run in the runtime's prefix with plain Wine."""
        installers = catalog.win32_installers(product.product_id)
        if not installers:
            raise InstallError("The Store lists no installer for this product.")
        order = {"x64": 0, "neutral": 1, "x86": 2}
        installers.sort(key=lambda i: order.get(i.arch.lower(), 9))
        chosen = installers[0]
        if not runtime.prefix_ready():
            raise InstallError(f"{runtime.name} has no prefix yet: finish setup in Settings.")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", product.title)[:60] or product.product_id
        suffix = ".msi" if chosen.kind.lower() == "msi" else ".exe"
        target = paths.downloads_dir() / f"{product.product_id}-{name}{suffix}"
        self._step(job, "downloading", f"Downloading {product.title}")

        def progress(done: int, total: int) -> None:
            job.done, job.total = done, total
            self._emit(job)
        net.download(chosen.url, target, progress=progress, cancelled=lambda: job.cancel_requested)
        if chosen.sha256:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest.lower() != chosen.sha256.lower():
                target.unlink(missing_ok=True)
                raise InstallError("Installer checksum mismatch; the download was discarded")

        start_menu = runtime.prefix / "drive_c" / "ProgramData" / "Microsoft" / "Windows" / "Start Menu"
        user_menu = runtime.prefix / "drive_c" / "users"
        before = {p for p in list(start_menu.rglob("*.lnk")) + list(user_menu.rglob("*.lnk"))}
        self._step(job, "installing", "Running the publisher's installer")
        if suffix == ".msi":
            argv = [str(runtime.wine), "msiexec", "/i", str(target)] + (["/qn"] if chosen.switches else [])
        else:
            argv = [str(runtime.wine), str(target)] + shlex.split(chosen.switches)
        for line in runtime.stream(argv, env=runtime.env({"WINEDEBUG": "-all"}), cancelled=lambda: job.cancel_requested):
            job.log.append(line)
        after = {p for p in list(start_menu.rglob("*.lnk")) + list(user_menu.rglob("*.lnk"))}
        new = sorted(after - before, key=lambda p: (("uninstall" in p.name.lower()), len(p.name)))
        if not new:
            raise InstallError("The installer finished but created no Start-menu shortcut to launch.")
        runtime.remember(f"win32-{product.product_id}", product_id=product.product_id, title=product.title,
                         logo_url=job.logo_url, shortcut=str(new[0]))
        if not settings.load().get("keep_downloads"):
            target.unlink(missing_ok=True)


_installer: Installer | None = None


def get() -> Installer:
    global _installer
    if _installer is None:
        _installer = Installer()
    return _installer
