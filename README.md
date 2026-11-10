# UWP Store

A native Linux storefront for Microsoft Store apps and games. The store itself is a GTK4/libadwaita app that
talks directly to the Store's public web services; only the apps you install run under Wine, through
[wine-uwp](https://github.com/designgears/wine-uwp). Games can use **GE-Proton UWP**: wine-uwp ported onto
GE-Proton's Wine, with GE's DXVK, vkd3d-proton and game fixes.

An earlier attempt ran Microsoft's own Store app inside Wine. It got as far as the home page, then crashed in
XAML/DirectComposition on navigation. This version drops that approach: browsing, search, product pages,
downloads, the library and sign-in are native, so the store itself does not depend on Windows UI frameworks.

UWP Store is an independent project, not affiliated with or endorsed by Microsoft. It ships no Microsoft files:
the Windows components apps need come from a Windows 11 ISO you provide, on your own machine.

## Install

Needs Python 3.10+, GTK 4, libadwaita 1.6+ and PyGObject (with libsecret for the keyring). On Arch:
`sudo pacman -S python-gobject gtk4 libadwaita libsecret`. On Fedora: `python3-gobject gtk4 libadwaita libsecret`.
On Debian/Ubuntu: `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-secret-1`.

```sh
git clone https://github.com/z5vix/uwp-store && cd uwp-store
./install-desktop.sh          # uwp-store command, icon and start-menu entry (in your home only)
./install-desktop.sh --uninstall
```

Installed apps run with [wine-uwp](https://github.com/designgears/wine-uwp): build it and put its distribution tree in
`~/.local/share/uwp-store/runtimes/<name>` or on `PATH` (or build GE-Proton UWP from Settings → Setup).

## Run

```sh
python3 -m uwpstore            # from this directory; needs gtk4, libadwaita and python-gobject
./uwp-store                    # the same, from anywhere
```

Options: `--page=home|apps|gaming|library`, `--product=PRODUCTID`, `--search=TEXT`, `--settings`, `--sign-in`.

Command line, for scripting and debugging:

```sh
./uwp-store search minecraft
./uwp-store product 9WZDNCRFHVN5
./uwp-store packages 9WZDNCRFHVN5     # what the delivery service offers, frameworks included
./uwp-store install 9WZDNCRFHVN5
./uwp-store runtimes
./uwp-store apps
./uwp-store run windowscalculator
./uwp-store store                     # Microsoft Store app: newest offered vs installed per runtime
./uwp-store install 9WZDNCRFJBMP --runtime v1-runtime-wow64   # install/update it in one runtime
./uwp-store signin | account | signout
./uwp-store menu                      # start-menu entries for installed apps (done automatically)
```

## Start menu

Every app you install gets its own start-menu entry, with the package's icon, and loses it again on uninstall
(Settings → Store → *Add installed apps to the start menu* turns this off). Helper packages the manifest keeps off
Windows' Start menu (`AppListEntry="none"`, such as StorePurchaseApp) stay off it here too. When the same app is in
two runtimes, each entry names its runtime. Entries are `~/.local/share/applications/uwpstore-*.desktop`.

## Games you own

Signed in, a paid product's page asks Microsoft's licence service (`collections.mp.microsoft.com`, as Wine-UWP does)
whether your account owns it:

- **Owned, with a Store package** (Minecraft for Windows, for example, which the delivery service offers as a plain
  `.appx`): the button is *Install* and it installs like a free app.
- **Owned Xbox PC (GDK/MSIXVC) games** (Minecraft Launcher, most Xbox titles): they have no Store package, so the
  button opens the game's page in the Xbox app (`msxbox://game/?productId=…`). The Xbox app installs it there with
  Wine-UWP's Gaming Services stand-in. Sign in inside the Xbox app once: it keeps its own sign-in per prefix.
- **Bundles** (Minecraft: Java & Bedrock) list their parts, each with its own page.
- Game Pass access is not ownership; those titles install through the Xbox app.

The Xbox app from 2612 starts `XboxPcAppCE.exe`, a launcher that hands off to the real app through `msxboxl:`, which
Wine-UWP does not route. The store points such packages at their real entry point (`LAUNCH_FIXES` in
`uwpstore/runtimes.py`).

## Microsoft account

The person icon in the header (or Settings → Store) signs in with Microsoft's device-code flow, the same one Wine-UWP's
Xbox sign-in uses (client `000000004C12AE6F`). The store asks login.live.com for a short code, opens
`https://www.microsoft.com/link?otc=CODE` in your browser, and waits while you sign in and approve there. Your password
never passes through the store. What it keeps is the refresh token, in your desktop keyring (libsecret). Without a
keyring it goes in `settings.json`, mode 0600. The header shows your gamertag (Xbox Live user.auth → XSTS). When
signed in, package lookups carry a Store delivery ticket (scope `service::dcat.update.microsoft.com::MBI_SSL`), so the
delivery service can offer the packages your account is licensed for.

## Latest Microsoft Store

[store.rg-adguard.net](https://store.rg-adguard.net) is a web front end for the Store's delivery service (FE3). Given
`https://apps.microsoft.com/detail/9wzdncrfjbmp?hl=en-US&gl=US` it lists the newest Microsoft Store packages. Its
API sits behind a Cloudflare browser check, so the store does not scrape it. It asks the same Microsoft service
directly, and gets the same files and SHA-1 digests (checked: `Microsoft.WindowsStore_22609.1401.13.0_neutral_~_8wekyb3d8bbwe.msixbundle`,
SHA-1 `6a2b7ca81b4975d3b27752cd76a0fecc3836a2bd`). Settings → Store → *Microsoft Store app* shows the newest version
against each runtime's install, with Install/Update buttons. Every product page offers **Update** when a newer
package exists than the installed one. Its *Packages* list shows each file's digest, a button to copy its signed
download link, and a shortcut that copies the product link and opens rg-adguard to compare.

## Setup: the Windows 11 ISO

UWP apps need Microsoft's own frameworks (XAML, the app model, CoreMessaging). They are **not shipped**: Settings →
Setup asks for a Windows 11 ISO you already have (or links Microsoft's free evaluation ISO), and wine-uwp's
harvester extracts the needed files from it on your machine. The harvested tree is shared by every runtime. If v1
already harvested one (`~/.local/share/ms-store-uwp/data*/windows`), v2 uses it.

## How it works

| Module | What |
| --- | --- |
| `uwpstore/catalog.py` | `storeedgefd.dsx.mp.microsoft.com/v9.0`: page layouts (home, gaming), collections, search, product details, publisher installers. No account. |
| `uwpstore/account.py` | Microsoft account: device-code sign-in, refresh token in the keyring, per-scope tickets, Xbox Live gamertag. |
| `uwpstore/desktop.py` | Start-menu entries for installed apps. |
| `uwpstore/fe3.py` | The Store's package delivery (FE3, the Windows Update protocol): a product's WuCategoryId → every package and framework → signed CDN URLs. Anonymous for free products. |
| `uwpstore/net.py` | HTTP with a disk cache (store opens offline from cache), resumable downloads, digest checks. The delivery hosts chain to *Microsoft Root Certificate Authority 2011*, which Linux CA bundles lack; that root (`microsoft-root-2011.pem`, SHA-256 `847DF6A7…ED7C61`) is trusted for `*.delivery.mp.microsoft.com` only. TLS verification is never disabled. |
| `uwpstore/installer.py` | Install queue: pick the newest unencrypted x64/neutral package, download and verify it, fetch any framework the Windows files lack (extracted into the profile's `stage/frameworks`), then `wine-uwp install`. Products the Store lists with a publisher installer (Win32 `.exe`/`.msi`) are downloaded, SHA-256 checked and run with the runtime's Wine. |
| `uwpstore/runtimes.py` | Discovers runtimes (wine-uwp distribution trees) and gives each one its own profile (prefix, stage, logs). v1's runtimes and data directories are adopted as they are. |
| `uwpstore/ui/` | The GTK4/libadwaita front end. |
| `proton-uwp/` | The GE-Proton UWP build pipeline (below). |

Data: `~/.local/share/uwp-store` (profiles, downloads, runtimes, harvested Windows files), cache
`~/.cache/uwp-store`, settings `~/.config/uwp-store/settings.json`.

## GE-Proton UWP

`proton-uwp/` builds wine-uwp on top of GE-Proton's Wine, reproducibly, from pinned sources:

1. `01-fetch.sh`: GE-Proton at `GE-Proton11-9` (only its `wine` and `wine-staging` submodules), wine-uwp at its pinned commit, and upstream Wine 11.20 (wine-uwp's base).
2. `02-prep-wine.sh`: runs the WINE PATCHING section of GE's own `protonprep-valve-staging.sh`, so the tree is exactly GE's: Valve Wine + staging + GE's hotfixes, game fixes, Wayland, FSR, ntsync.
3. `03-port.py`: a three-way merge per file wine-uwp changed (base = Wine 11.20, theirs = wine-uwp, ours = GE). Generated files are regenerated instead. Hand resolutions in `resolutions/*.patch` are applied on top; `PORT-STATUS.md` lists what remains.
4. `04-build.sh`: regenerate, configure like Proton (`--enable-archs=x86_64,i386`), build.
5. `05-export.sh`: save hand fixes as `resolutions/*.patch`.
6. `06-package.sh [--steam]`: a wine-uwp runtime in `~/.local/share/uwp-store/runtimes/GE-Proton11-9-UWP` that uses GE's DXVK, vkd3d-proton and NVAPI, and with `--steam` also a `GE-Proton11-9-UWP` Steam compatibility tool (a copy of GE's release with the ported Wine laid over it; GE's own install is never modified).

Settings → GE-Proton UWP runs the whole pipeline. Games then install into that runtime's prefix; apps keep using the
plain wine-uwp runtime (both are selectable in Settings).

## Limits

- Buying happens on Microsoft's site: paid products you do not own link to their web page.
- Whether an owned game *runs* depends on Wine-UWP answering its licence check at run time; installing it is not proof. Nothing bypasses licensing, and encrypted packages (`.eappx`, `.emsix`) are refused.
- Xbox PC games shipped as MSIXVC install only through the Xbox app (see *Games you own*).
- Apps built on frameworks the Windows image and the Store do not provide (Windows 8.1 JavaScript apps that need WinJS) cannot install; the store says which framework is missing.
- Whether an installed app runs is up to wine-uwp: see its compatibility notes. Calculator, Solitaire Collection, Media Player and Feedback Hub are known to work.

## License

LGPL-2.1-or-later, like Wine. See `LICENSE`.
