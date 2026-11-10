"""UWP runtimes: Wine builds that can install and run packaged apps.

Every runtime is a wine-uwp distribution tree (bin/wine-uwp, bin/wine, share/wine-uwp), whatever Wine it
was built from. Two flavours are recognised:

  wine-uwp     upstream wine-uwp on Wine 11.20 (what v1 built)
  proton-uwp   wine-uwp ported onto GE-Proton's Wine (proton-uwp/ in this repository), with GE's DXVK,
               vkd3d-proton and Proton's environment defaults; meant for games

Each runtime gets its own profile directory (prefix, stage, logs), because an installed app's launch
files name absolute paths inside its runtime's profile. The Windows components harvested from the
user's ISO are shared by all profiles through a symlink, so the ISO is harvested once.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from . import paths, settings

LEGACY_HOME = Path.home() / ".local/share/ms-store-uwp"

# Proton's defaults for games, applied only to proton-uwp runtimes. A variable already set wins.
PROTON_GAME_ENV = {
    "WINEFSYNC": "1",
    "WINE_LARGE_ADDRESS_AWARE": "1",
    "DXVK_ENABLE_NVAPI": "1",
    "PROTON_ENABLE_NVAPI": "1",
    "VKD3D_FEATURE_LEVEL": "12_2",
    "__GL_SHADER_DISK_CACHE_SKIP_CLEANUP": "1",
}


class RuntimeError_(Exception):
    pass


@dataclass
class InstalledApp:
    stage: str                    # directory name under stage/
    full_name: str
    family_name: str
    aumid: str
    title: str
    exe: str
    runtime_id: str
    product_id: str = ""
    logo_url: str = ""
    kind: str = "uwp"             # uwp | win32
    shortcut: str = ""            # win32: the Start-menu shortcut that launches it
    icon_path: str = ""           # the package's own list icon, when it has one

    in_app_list: bool = True       # false for helper packages the manifest keeps off the Start menu

    @property
    def launchable(self) -> bool:
        return self.kind == "win32" or bool(self.exe)

    @property
    def version(self) -> str:
        """The package version from Name_Version_Arch_Resource_PublisherId ("" for win32 apps)."""
        parts = self.full_name.split("_")
        return parts[1] if len(parts) >= 5 else ""


@dataclass
class Runtime:
    id: str
    name: str
    flavour: str                  # wine-uwp | proton-uwp
    root: Path
    wine_version: str = ""
    d3d_runtime: Path | None = None
    extra_env: dict[str, str] = field(default_factory=dict)
    profile_dir: Path | None = None   # adopt an existing wine-uwp data directory (v1's) instead of our own

    # ---- layout ----

    @property
    def launcher(self) -> Path:
        return self.root / "bin" / "wine-uwp"

    @property
    def wine(self) -> Path:
        return self.root / "bin" / "wine"

    @property
    def wineserver(self) -> Path:
        return self.root / "bin" / "wineserver"

    @property
    def share(self) -> Path:
        return self.root / "share" / "wine-uwp"

    @property
    def is_proton(self) -> bool:
        return self.flavour == "proton-uwp"

    @property
    def profile(self) -> Path:
        path = self.profile_dir or paths.data_dir() / "profiles" / self.id
        (path / "logs").mkdir(parents=True, exist_ok=True)
        return path

    @property
    def prefix(self) -> Path:
        return self.profile / "prefix"

    @property
    def stage_dir(self) -> Path:
        return self.profile / "stage"

    @property
    def meta_dir(self) -> Path:
        path = self.profile / "store-meta"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- state ----

    def usable(self) -> bool:
        return self.launcher.is_file() and self.wine.exists()

    def windows_ready(self) -> bool:
        return (self.profile / "windows" / "Windows" / "System32" / "config" / "SOFTWARE").is_file()

    def prefix_ready(self) -> bool:
        return (self.prefix / ".wine-uwp-ready").is_file()

    def ready(self) -> bool:
        return self.usable() and self.windows_ready() and self.prefix_ready()

    def link_windows_tree(self) -> None:
        """Point this profile's windows/ at the shared harvested tree."""
        shared = shared_windows_tree()
        link = self.profile / "windows"
        if shared is None:
            return
        if link.is_symlink():
            if Path(os.readlink(link)) == shared:
                return
            link.unlink()
        elif link.exists():
            return  # a real tree harvested into this profile; keep it
        link.symlink_to(shared, target_is_directory=True)

    # ---- environment ----

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env["WINE_UWP_DATA"] = str(self.profile)
        env["WINEPREFIX"] = str(self.prefix)
        from . import webview2
        if webview2.available() and not (self.profile / "webview2" / "rt").is_dir():
            # the runtime installs it into the prefix on the next run (install-overrides.sh honours this); its setup
            # skips work when its stamp is current, so force one pass while the prefix still lacks the runtime
            env.setdefault("WEBVIEW2_RUNTIME", str(webview2.root()))
            if not (self.prefix / "drive_c" / "Program Files (x86)" / "Microsoft" / "EdgeWebView").is_dir():
                env.setdefault("WINE_UWP_FORCE_OVERRIDES", "1")
        if self.d3d_runtime:
            env["WINE_UWP_D3D_RUNTIME"] = str(self.d3d_runtime)
        if self.is_proton:
            for key, value in PROTON_GAME_ENV.items():
                env.setdefault(key, value)
        env.update(self.extra_env)
        env.update(extra or {})
        return env

    def command(self, *args: str) -> list[str]:
        return [str(self.launcher), *args]

    # ---- operations (each yields output lines; the caller drives the generator off the UI thread) ----

    def stream(self, argv: list[str], env: dict[str, str] | None = None,
               cancelled: Callable[[], bool] | None = None, cwd: Path | None = None) -> Iterator[str]:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   env=env or self.env(), cwd=cwd, start_new_session=True, bufsize=1,
                                   errors="replace")
        assert process.stdout is not None
        errors: list[str] = []
        try:
            for line in process.stdout:
                if cancelled and cancelled():
                    os.killpg(process.pid, signal.SIGTERM)
                    raise RuntimeError_("Cancelled")
                line = line.rstrip("\n")
                if re.search(r":err:|failed|error", line, re.I) and "MESA" not in line:
                    errors.append(line)
                yield line
        finally:
            code = process.wait()
        if code != 0:
            detail = errors[-1] if errors else f"exited with status {code}"
            # drop Wine's "00e4:err:channel:function " prefix
            detail = re.sub(r"^[0-9a-f]{4}:err:[\w]+:[\w]+ ", "", detail)
            raise RuntimeError_(f"{Path(argv[0]).name} {' '.join(argv[1:2])}: {detail}")

    def harvest(self, iso: Path, cancelled=None) -> Iterator[str]:
        target = paths.windows_dir()
        target.mkdir(parents=True, exist_ok=True)
        script = self.share / "packaging" / "harvest" / "harvest.py"
        yield "STAGE Extracting Windows components from the ISO"
        yield from self.stream(["python3", str(script), str(iso), str(target)], cancelled=cancelled)
        self.link_windows_tree()

    def new_prefix(self, cancelled=None) -> Iterator[str]:
        self.link_windows_tree()
        if not self.windows_ready():
            raise RuntimeError_("No harvested Windows components yet. Choose your Windows 11 ISO first.")
        yield from self.stream(self.command("new-prefix"), cancelled=cancelled)

    def install(self, package: Path, cancelled=None) -> Iterator[str]:
        yield from self.stream(self.command("install", str(package)), cancelled=cancelled)

    def info(self, package: Path) -> dict:
        out = subprocess.run(self.command("info", str(package)), env=self.env(), capture_output=True, text=True,
                             timeout=300)
        try:
            return json.loads(out.stdout)
        except ValueError as exc:
            raise RuntimeError_(out.stderr.strip() or "Could not read the package") from exc

    # ---- apps ----

    def apps(self) -> list[InstalledApp]:
        out: list[InstalledApp] = []
        if self.stage_dir.is_dir():
            for identity in sorted(self.stage_dir.glob("*/identity.env")):
                stage = identity.parent.name
                if stage.endswith((".new", ".old")) or stage == "frameworks":
                    continue
                fix_launch(identity.parent)
                values = _read_env(identity)
                app = InstalledApp(
                    stage=stage,
                    full_name=values.get("WINE_UWP_PACKAGE_FULL_NAME", ""),
                    family_name=values.get("WINE_UWP_PACKAGE_FAMILY_NAME", ""),
                    aumid=values.get("WINE_UWP_AUMID", ""),
                    title=values.get("WINE_UWP_WINDOW_TITLE", stage),
                    exe=values.get("UWP_EXE", ""),
                    runtime_id=self.id,
                )
                _apply_meta(app, self.meta_dir / f"{stage}.json")
                app.icon_path = _package_icon(identity.parent)
                app.in_app_list = app.family_name in LAUNCH_FIXES or not _hidden_from_app_list(identity.parent, app.aumid)
                out.append(app)
        for meta in sorted(self.meta_dir.glob("win32-*.json")):
            data = _load_json(meta)
            if data.get("shortcut"):
                out.append(InstalledApp(stage=meta.stem, full_name="", family_name="", aumid="",
                                        title=data.get("title", meta.stem), exe="", runtime_id=self.id,
                                        product_id=data.get("product_id", ""), logo_url=data.get("logo_url", ""),
                                        kind="win32", shortcut=data["shortcut"]))
        return out

    def activate(self, uri: str) -> None:
        """Hand a URI to the installed app that owns its scheme (starting the app if needed)."""
        subprocess.Popen(self.command("activate", uri), env=self.env(), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def find_app(self, family_name: str = "", product_id: str = "") -> InstalledApp | None:
        for app in self.apps():
            if product_id and app.product_id == product_id:
                return app
            if family_name and app.family_name.lower() == family_name.lower():
                return app
        return None

    def run(self, app: InstalledApp) -> subprocess.Popen:
        log = (self.profile / "logs" / f"{app.stage}.store.log").open("ab")
        if app.kind == "win32":
            argv = [str(self.wine), "start", "/unix", app.shortcut]
        else:
            argv = self.command("run", app.stage)
        return subprocess.Popen(argv, env=self.env(), stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)

    def stop(self, app: InstalledApp) -> None:
        if app.kind == "win32":
            subprocess.run([str(self.wineserver), "-k"], env=self.env(), timeout=30)
        else:
            subprocess.run(self.command("stop", app.stage), env=self.env(), timeout=60)

    def running(self, app: InstalledApp) -> bool:
        lock = self.profile / "logs" / f".{app.stage}.running"
        if not lock.exists():
            return False
        try:
            import fcntl
            with lock.open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
            return False
        except OSError:
            return True

    def uninstall(self, app: InstalledApp) -> None:
        if self.running(app):
            self.stop(app)
        if app.kind == "uwp":
            target = self.stage_dir / app.stage
            if target.is_dir() and target.parent == self.stage_dir:
                shutil.rmtree(target)
        (self.meta_dir / f"{app.stage}.json").unlink(missing_ok=True)

    def remember(self, stage: str, **meta: str) -> None:
        path = self.meta_dir / f"{stage}.json"
        data = _load_json(path)
        data.update(meta)
        path.write_text(json.dumps(data, indent=2))


# Packages whose first <Application> is only a launcher that hands off through a URI scheme wine-uwp does not route.
# The Xbox app (2612+) starts XboxPcAppCE.exe, which opens msxboxl:/// for the real app, Microsoft.Xbox.AppL.
LAUNCH_FIXES = {
    "Microsoft.GamingApp_8wekyb3d8bbwe": ("Microsoft.Xbox.AppL", "XboxPcApp.exe"),
}
XBOX_APP_FAMILY = "Microsoft.GamingApp_8wekyb3d8bbwe"


def fix_launch(stage: Path) -> bool:
    """Point a stage's identity.env at the package's real entry point (see LAUNCH_FIXES). True if changed."""
    identity = stage / "identity.env"
    values = _read_env(identity)
    fix = LAUNCH_FIXES.get(values.get("WINE_UWP_PACKAGE_FAMILY_NAME", ""))
    if not fix or not (stage / fix[1]).is_file():
        return False
    family = values["WINE_UWP_PACKAGE_FAMILY_NAME"]
    aumid = f"{family}!{fix[0]}"
    if values.get("WINE_UWP_AUMID") == aumid and values.get("UWP_EXE") == fix[1]:
        return False
    lines = []
    for line in identity.read_text().splitlines():
        if line.startswith("export WINE_UWP_AUMID="):
            line = f"export WINE_UWP_AUMID={aumid}"
        elif line.startswith("export UWP_EXE="):
            line = f"export UWP_EXE='{fix[1]}'"
        lines.append(line)
    identity.write_text("\n".join(lines) + "\n")
    return True


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("export ") or "=" not in line:
            continue
        key, _, raw = line[7:].partition("=")
        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = [raw]
        values[key.strip()] = parts[0] if parts else ""
    return values


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


_ICON_ATTR = re.compile(r'Square44x44Logo="([^"]+)"|<Logo>([^<]+)</Logo>')


def _ci_file(root: Path, relative: str) -> Path | None:
    """root/relative, matching each part case-insensitively (manifests say "assets", packages ship "Assets")."""
    current = root
    for part in [p for p in relative.split("/") if p]:
        exact = current / part
        if exact.exists():
            current = exact
            continue
        try:
            lowered = {child.name.lower(): child for child in current.iterdir()}
        except OSError:
            return None
        found = lowered.get(part.lower())
        if found is None:
            return None
        current = found
    return current


_ICON_VARIANTS = ("targetsize-256_altform-unplated", "targetsize-256", "targetsize-96_altform-unplated", "targetsize-96",
                  "targetsize-48_altform-unplated", "targetsize-48", "scale-400", "scale-200", "scale-150", "scale-100", "")


def _package_icon(package_dir: Path) -> str:
    """The best rendition of the manifest's list icon (Square44x44Logo, else Logo), as a file path."""
    try:
        manifest = (package_dir / "AppxManifest.xml").read_text(errors="replace")
    except OSError:
        return ""
    for match in _ICON_ATTR.finditer(manifest):
        relative = (match.group(1) or match.group(2)).replace("\\", "/")
        folder_rel, _, name = relative.rpartition("/")
        folder = _ci_file(package_dir, folder_rel) if folder_rel else package_dir
        if folder is None or not folder.is_dir():
            continue
        stem, dot, suffix = name.rpartition(".")
        stem, suffix = (stem, "." + suffix) if dot else (name, ".png")
        names = {child.name.lower(): child for child in folder.iterdir()}
        for variant in _ICON_VARIANTS:
            candidate = names.get((f"{stem}.{variant}{suffix}" if variant else f"{stem}{suffix}").lower())
            if candidate and candidate.is_file():
                return str(candidate)
    return ""


_APPLICATION = re.compile(r"<Application\b[^>]*\bId=\"([^\"]+)\"(.*?)</Application>", re.S)


def _hidden_from_app_list(package_dir: Path, aumid: str) -> bool:
    """Whether the manifest keeps this application out of the Start menu (AppListEntry="none"), as Windows does."""
    app_id = aumid.partition("!")[2]
    try:
        manifest = (package_dir / "AppxManifest.xml").read_text(errors="replace")
    except OSError:
        return False
    for match in _APPLICATION.finditer(manifest):
        if match.group(1) == app_id:
            return bool(re.search(r'<[\w:]*VisualElements\b[^>]*AppListEntry="none"', match.group(2)))
    return False


def _apply_meta(app: InstalledApp, path: Path) -> None:
    data = _load_json(path)
    app.product_id = data.get("product_id", "")
    app.logo_url = data.get("logo_url", "")
    if data.get("title"):
        app.title = data["title"]


# ---- discovery ---------------------------------------------------------------------------------

def shared_windows_tree() -> Path | None:
    """The harvested Windows tree: ours, a configured one, or the one v1 harvested."""
    candidates = [paths.windows_dir()]
    configured = settings.load().get("windows_tree")
    if configured:
        candidates.insert(0, Path(configured).expanduser())
    candidates += [LEGACY_HOME / "data-wow64" / "windows", LEGACY_HOME / "data" / "windows"]
    for path in candidates:
        if (path / "Windows" / "System32" / "config" / "SOFTWARE").is_file():
            return path.resolve()
    return None


def _wine_version(root: Path) -> str:
    version_file = root / "share" / "wine-uwp" / "VERSION"
    if version_file.is_file():
        return version_file.read_text().strip().splitlines()[0][:60]
    return ""


def _from_tree(root: Path, fallback_name: str) -> Runtime | None:
    if not (root / "bin" / "wine-uwp").is_file():
        return None
    meta = _load_json(root / "runtime.json")
    flavour = meta.get("flavour", "wine-uwp")
    runtime = Runtime(
        id=meta.get("id") or re.sub(r"[^a-z0-9.-]+", "-", fallback_name.lower()).strip("-"),
        name=meta.get("name", fallback_name),
        flavour=flavour,
        root=root,
        wine_version=meta.get("wine_version") or _wine_version(root),
        extra_env=meta.get("env") or {},
    )
    d3d = meta.get("d3d_runtime")
    if d3d and Path(d3d).is_dir():
        runtime.d3d_runtime = Path(d3d)
    elif (root / "lib" / "d3d-runtime" / "x86_64-windows").is_dir():
        runtime.d3d_runtime = root / "lib" / "d3d-runtime" / "x86_64-windows"
    return runtime


def discover() -> list[Runtime]:
    found: list[Runtime] = []
    for tree in sorted(paths.runtimes_dir().iterdir()) if paths.runtimes_dir().is_dir() else []:
        runtime = _from_tree(tree, tree.name)
        if runtime:
            found.append(runtime)
    for name, data, label in (("runtime-wow64", "data-wow64", "Wine-UWP 11.20 (v1 build, WoW64)"),
                              ("runtime", "data", "Wine-UWP 11.20 (v1 build)")):
        runtime = _from_tree(LEGACY_HOME / name, label)
        if runtime:
            runtime.id = f"v1-{name}"
            # v1 set up a prefix and installed apps in its own data directory; keep using it
            if (LEGACY_HOME / data / "prefix").is_dir():
                runtime.profile_dir = LEGACY_HOME / data
            found.append(runtime)
    on_path = shutil.which("wine-uwp")
    if on_path:
        root = Path(on_path).resolve().parent.parent
        if all(r.root.resolve() != root for r in found):
            runtime = _from_tree(root, "Wine-UWP (system)")
            if runtime:
                found.append(runtime)
    return found


def by_id(runtime_id: str) -> Runtime | None:
    for runtime in discover():
        if runtime.id == runtime_id:
            return runtime
    return None


def default_runtime() -> Runtime | None:
    cfg = settings.load()
    all_runtimes = discover()
    for runtime in all_runtimes:
        if runtime.id == cfg.get("runtime"):
            return runtime
    plain = [r for r in all_runtimes if not r.is_proton]
    return (plain or all_runtimes or [None])[0]


def game_runtime() -> Runtime | None:
    cfg = settings.load()
    all_runtimes = discover()
    for runtime in all_runtimes:
        if runtime.id == cfg.get("game_runtime"):
            return runtime
    proton = [r for r in all_runtimes if r.is_proton]
    return proton[0] if proton else default_runtime()


def runtime_for(is_game: bool) -> Runtime | None:
    return game_runtime() if is_game else default_runtime()


def all_apps() -> list[InstalledApp]:
    apps: list[InstalledApp] = []
    for runtime in discover():
        if runtime.profile_dir or (paths.data_dir() / "profiles" / runtime.id).is_dir():
            apps.extend(runtime.apps())
    return apps
