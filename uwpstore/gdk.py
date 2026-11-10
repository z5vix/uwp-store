"""GDK games (MSIXVC packages: Minecraft for Windows, the Minecraft Launcher, Halo Infinite...) without the Xbox app.

Windows installs these through the Xbox app and Gaming Services. Wine-UWP does everything that takes in its own DLLs:
winexboxauth.dll signs the Xbox Live requests and gets the content key from the account's Store licence, and
appxinstall.dll (wineuwp-install) streams the encrypted .msixvc from the Xbox CDN, decrypts it and lays the game out
in a stage. The store drives the same pieces directly:

  1. put the account's Microsoft sign-in into the runtime's prefix (the store's refresh token is for the same
     Microsoft client the Xbox app uses), through helpers/xboxbridge.exe, keeping the prefix's per-account vault
     (xboxaccounts) in step;
  2. ask packagespc.xboxlive.com for the title's base package (content id from the display catalogue);
  3. get the content keys for it (the account must own the title: this is the licence check, nothing is bypassed);
  4. run wineuwp-install on the CDN URL with the keys in WINE_UWP_CONTENT_KEYS (the installer clears it at once).

A game whose MicrosoftGame.Config names its own MSAAppId signs in to Xbox Live as that Microsoft client (or, for
Minecraft's 0000000040159362, which only Windows' account broker redeems, as Minecraft's Android client 0000000048183522,
as BedrockOnLinux does), and Microsoft gives a refresh token for it only to a sign-in the user approves for that client. Before
its first start the store runs that device-code sign-in once (title_signin_needed / import_title_signin) and hands the
token to the prefix; without it the game cannot get its title token and shows "Your game is missing a required
component".

  5. install the redistributables the game ships in its Installers folder (Minecraft's GameInputRedist.msi: without
     the GameInput v2 runtime it stops at "Your game is missing a required component"), as the Xbox app does.

The keys never touch the disk or a log. The GDK stage gets its own name when a UWP install of the same package
already uses the default one (Minecraft's last UWP build is "minecraftuwp").
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from . import account, catalog, fe3, net, packages, paths, runtimes

HELPER_SOURCE = Path(__file__).parent / "helpers" / "xboxbridge.c"
MARKER = ".uwpstore-gdk"

# Store apps whose delivery categories carry the frameworks GDK games declare. A GDK title's own package lists no
# framework files, and Windows gets them from the Store when the Xbox app installs it. Paint and Snipping Tool carry
# Windows App SDK 1.8 (Minecraft needs Microsoft.WindowsAppRuntime.1.8).
FRAMEWORK_CARRIERS = ("9PCFS5B6T72H", "9MZ95KL8MR0L", "9WZDNCRFJBMP", "9MV0B5HZVK9Z", "9N0DX20HK701")


class GdkError(Exception):
    pass


@dataclass
class GdkPackage:
    name: str           # Microsoft.MinecraftUWP
    version: str
    file_name: str
    size: int
    url: str


def helper() -> Path:
    """xboxbridge.exe: the shipped build, or one built from the source with mingw-w64 when it is missing."""
    shipped = HELPER_SOURCE.with_suffix(".exe")
    if shipped.is_file():
        return shipped
    built = paths.data_dir() / "helpers" / "xboxbridge.exe"
    if built.is_file() and built.stat().st_mtime >= HELPER_SOURCE.stat().st_mtime:
        return built
    compiler = shutil.which("x86_64-w64-mingw32-gcc")
    if not compiler:
        raise GdkError("GDK games need helpers/xboxbridge.exe; install mingw-w64 (x86_64-w64-mingw32-gcc) to build it")
    built.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([compiler, "-O2", "-o", str(built), str(HELPER_SOURCE), "-lole32"], check=True,
                   capture_output=True, text=True)
    return built


def _bridge(runtime: runtimes.Runtime, *args: str, stdin: str | None = None, timeout: int = 300) -> str:
    env = runtime.env({"WINEDEBUG": "-all"})
    process = subprocess.run([str(runtime.wine), str(helper()), *args], env=env, input=stdin, capture_output=True,
                             text=True, timeout=timeout, errors="replace")
    if process.returncode == 3:
        raise GdkError(f"{runtime.name}'s Wine-UWP is too old for GDK installs from the store (no "
                       "XboxAuthImportRefreshToken); rebuild it with the store's Wine-UWP patches")
    if process.returncode != 0:
        lines = [l for l in process.stderr.splitlines() if l.startswith("xboxbridge:")]
        raise GdkError(lines[-1].split(":", 1)[1].strip() if lines else f"xboxbridge {args[0]} failed")
    return process.stdout


def sign_in(runtime: runtimes.Runtime, account_id: str) -> None:
    """Make the prefix's Xbox Live sign-in this store account (the Xbox app and games use the same one)."""
    from . import xboxaccounts
    profile = account.get(account_id) if account_id else account.active()
    if profile is None:
        raise GdkError("Sign in with the Microsoft account that owns this game first")
    if profile.xuid and xboxaccounts.adopt(runtime, profile.xuid):
        return                                          # already this account (or restored from its vault)
    refresh = account._load_refresh(profile.id)
    if not refresh:
        raise GdkError(f"{profile.name} has no saved sign-in; sign in again")
    user_id = account._token_grant(profile.id, account.SIGNIN_SCOPE).get("user_id", "")
    _bridge(runtime, "import", stdin=f"{refresh}\n{user_id}\n")
    if profile.xuid:
        xboxaccounts.mark_signed_in(runtime, profile.xuid)


def owns(runtime: runtimes.Runtime, product_id: str) -> bool:
    return _bridge(runtime, "owns", product_id).strip() == "owned"


def base_package(runtime: runtimes.Runtime, content_id: str) -> GdkPackage:
    out = _bridge(runtime, "get", f"https://packagespc.xboxlive.com/GetBasePackage/{content_id}")
    head, _, body = out.partition("\n")
    status = head.split()[-1] if head.startswith("STATUS") else "?"
    if status != "200":
        raise GdkError(f"Xbox Live has no package for this game (HTTP {status})")
    data = json.loads(body)
    for item in data.get("PackageFiles") or []:
        name = item.get("FileName") or ""
        if not name.lower().endswith(".msixvc"):
            continue
        roots = item.get("CdnRootPaths") or []
        if not roots:
            continue
        match = re.match(r"(?P<name>[^_]+)_(?P<version>[\d.]+)_", name)
        return GdkPackage(name=match["name"] if match else name, version=match["version"] if match else "",
                          file_name=name, size=int(item.get("FileSize") or 0),
                          url=roots[0] + (item.get("RelativeUrl") or ""))
    raise GdkError("Xbox Live lists no .msixvc package for this game")


def _installer_env(runtime: runtimes.Runtime, keys: str) -> dict[str, str]:
    base = runtime.env()
    return runtime.env({"WINE_UWP_CONTENT_KEYS": keys, "PATH": f"{runtime.root / 'bin'}:{base.get('PATH', '')}"})


def missing_frameworks(runtime: runtimes.Runtime, url: str, keys: str) -> list[packages.Dependency]:
    """The frameworks the game declares that neither the Windows files nor earlier installs provide."""
    data = runtime.profile
    process = subprocess.run([str(runtime.wine), "wineuwp-install", url, str(data), "--info", "--windows",
                              str(data / "windows")], env=_installer_env(runtime, keys) | {"WINEDEBUG": "-all"},
                             capture_output=True, text=True, timeout=900, errors="replace")
    start = process.stdout.find("{")
    if process.returncode != 0 or start < 0:
        raise GdkError("Could not read the game's package: " + (process.stderr.strip().splitlines() or ["?"])[-1])
    info = json.loads(process.stdout[start:])
    roots = [runtime.stage_dir / "frameworks", runtime.profile / "windows" / "Program Files" / "WindowsApps"]
    out = []
    for item in info.get("frameworks") or []:
        if item.get("available"):
            continue
        dep = packages.Dependency(item.get("name", ""), item.get("min_version", "0.0.0.0"), "")
        if not packages.installed_framework(roots, dep):
            out.append(dep)
    return out


def provide_frameworks(runtime: runtimes.Runtime, needed: list[packages.Dependency],
                       cancelled: Callable[[], bool] | None = None) -> Iterator[str]:
    """Fetch each framework from the Store delivery of an app that carries it, into the profile's frameworks."""
    from .installer import choose_framework
    client = fe3.DeliveryClient(account.store_ticket())
    root = runtime.stage_dir / "frameworks"
    root.mkdir(parents=True, exist_ok=True)
    for dep in needed:
        found = None
        for carrier in FRAMEWORK_CARRIERS:
            try:
                resolution = client.resolve(catalog.product(carrier).category_id)
            except Exception:
                continue
            found = choose_framework(resolution, dep)
            if found:
                break
        if found is None:
            raise GdkError(f"Needs the {dep.name} framework (>= {dep.min_version}), which the Store gave no copy of")
        target = paths.downloads_dir() / found.local_name
        yield f"STAGE Downloading {found.name} {found.version}"
        if not (target.exists() and target.stat().st_size == found.size):
            net.download(client.url_for(found), target, expected_size=found.size, cancelled=cancelled or (lambda: False))
        if found.digest and not net.verify_digest(target, found.digest, found.digest_alg):
            target.unlink(missing_ok=True)
            raise GdkError(f"{found.name}: checksum mismatch; the download was discarded")
        packages.extract_package(target, root / found.moniker)
        target.unlink(missing_ok=True)


def _stage_for(runtime: runtimes.Runtime, package: GdkPackage) -> str:
    base = re.sub(r"[^a-z0-9]", "", package.name.rsplit(".", 1)[-1].lower()) or "game"
    existing = runtime.stage_dir / base
    if not existing.exists() or (existing / MARKER).exists():
        return base
    return base + "gdk"


def install(runtime: runtimes.Runtime, product_id: str, content_id: str, account_id: str = "",
            cancelled: Callable[[], bool] | None = None) -> Iterator[str]:
    """Install (or update) a GDK title; yields STAGE/PROGRESS lines like runtime.install."""
    yield "STAGE Signing in to Xbox Live"
    sign_in(runtime, account_id)
    if not owns(runtime, product_id):
        raise GdkError("This account does not own the game")
    yield "STAGE Finding the game's package"
    package = base_package(runtime, content_id)
    yield f"STAGE {package.file_name} ({package.size / 1e9:.1f} GB)"
    keys = _bridge(runtime, "keys", content_id).strip()
    if not keys:
        raise GdkError("Microsoft's licence service gave no content key for this game")
    yield "STAGE Checking the frameworks it needs"
    needed = missing_frameworks(runtime, package.url, keys)
    if needed:
        yield from provide_frameworks(runtime, needed, cancelled)
    stage = _stage_for(runtime, package)
    data = runtime.profile
    env = _installer_env(runtime, keys)
    keys = ""
    argv = [str(runtime.wine), "wineuwp-install", package.url, str(data), "--windows", str(data / "windows"),
            "--stage", stage]
    yield f"STAGE Downloading and installing {package.name} {package.version}"
    yield from runtime.stream(argv, env=env, cancelled=cancelled)
    target = runtime.stage_dir / stage
    if (target / "identity.env").is_file():
        (target / MARKER).write_text(json.dumps({"product_id": product_id, "content_id": content_id,
                                                 "version": package.version}) + "\n")
        yield "STAGE Installing the game's redistributables"
        install_redistributables(runtime, stage)


# ---- redistributables ------------------------------------------------------------------------------------

def _marker(runtime: runtimes.Runtime, stage: str) -> dict:
    try:
        return json.loads((runtime.stage_dir / stage / MARKER).read_text())
    except (OSError, ValueError):
        return {}


def _redistributables(runtime: runtimes.Runtime, stage: str) -> list[Path]:
    folder = runtime.stage_dir / stage / "Installers"
    return sorted(p for p in folder.glob("*.msi") if p.is_file()) if folder.is_dir() else []


def redistributables_pending(runtime: runtimes.Runtime, stage: str) -> list[Path]:
    """The game's Installers/*.msi not yet installed in this prefix for this version of the game."""
    marker = _marker(runtime, stage)
    if not marker:
        return []                                        # not a GDK stage of the store's
    done = marker.get("redistributables") or {}
    return [p for p in _redistributables(runtime, stage) if done.get(p.name) != p.stat().st_size]


def install_redistributables(runtime: runtimes.Runtime, stage: str) -> list[str]:
    """Run the game's own redistributable installers silently in the prefix (Microsoft packages shipped inside the
    game's package, which Windows' Xbox app runs at install time). Returns the ones that failed."""
    pending = redistributables_pending(runtime, stage)
    if not pending:
        return []
    marker = _marker(runtime, stage)
    done = dict(marker.get("redistributables") or {})
    failed = []
    for msi in pending:
        windows_path = "Z:" + str(msi).replace("/", "\\")
        process = subprocess.run([str(runtime.wine), "msiexec", "/i", windows_path, "/qn", "/norestart"],
                                 env=runtime.env({"WINEDEBUG": "-all"}), capture_output=True, timeout=900)
        if process.returncode in (0, 3010):              # 3010: done, a reboot would finish it
            done[msi.name] = msi.stat().st_size
        else:
            failed.append(msi.name)
    marker["redistributables"] = done
    (runtime.stage_dir / stage / MARKER).write_text(json.dumps(marker) + "\n")
    return failed


# ---- the title's own Microsoft client ------------------------------------------------------------------------

# MSAAppIds that only Windows' account broker redeems (Xbox Live's SISU answers 401), and the same game's client that
# SISU takes instead; Wine-UWP's winexboxauth has the same table (sisu_client_for).
SISU_CLIENT_ALIASES = {"0000000040159362": "0000000048183522"}      # Minecraft for Windows -> Minecraft (Android)


def title_client(runtime: runtimes.Runtime, app: runtimes.InstalledApp) -> str:
    """The Microsoft client a GDK game's Xbox Live sign-in needs ("" when it uses the Xbox app's client or is not a GDK
    game): its MSAAppId, or the client that stands in for it."""
    config = runtime.stage_dir / app.stage / "MicrosoftGame.Config"
    try:
        match = re.search(r"<MSAAppId>\s*([0-9A-Fa-f]{16})\s*</MSAAppId>", config.read_text(errors="replace"))
    except OSError:
        return ""
    client = match.group(1).upper() if match else ""
    client = SISU_CLIENT_ALIASES.get(client, client)
    return "" if client == account.CLIENT_ID else client


def title_signin_needed(runtime: runtimes.Runtime, app: runtimes.InstalledApp) -> str:
    """The client id the game still needs a sign-in for in this prefix, or ""."""
    client = title_client(runtime, app)
    if not client:
        return ""
    try:
        return "" if _bridge(runtime, "hasclient", client, timeout=120).strip() == "yes" else client
    except GdkError:
        return ""                                        # an older Wine-UWP: let the game say what it misses


def signed_in_account(runtime: runtimes.Runtime) -> account.Profile | None:
    from . import xboxaccounts
    xuid = xboxaccounts.signed_in_as(runtime)
    return account.get(xuid) if xuid else None


def import_title_signin(runtime: runtimes.Runtime, client: str, grant: dict) -> None:
    """Hand a device-code token response for the game's client to the prefix. The approval must come from the account
    the prefix is signed in as: another account's token would sign the game in as someone who may not own it."""
    from . import xboxaccounts
    refresh = grant.get("refresh_token") or ""
    if not refresh:
        raise GdkError("Microsoft gave no lasting sign-in for the game; try again")
    profile = signed_in_account(runtime)
    if profile is not None:
        expected = account._token_grant(profile.id, account.SIGNIN_SCOPE).get("user_id", "")
        got = grant.get("user_id", "")
        if expected and got and expected.lower() != got.lower():
            raise GdkError(f"That was a different Microsoft account; approve as {profile.name}")
    _bridge(runtime, "importclient", client, stdin=f"{refresh}\n", timeout=120)
    xboxaccounts._save_current(runtime)
