"""Microsoft Store catalogue: the storefront service the Windows Store app itself reads.

Pages (home, apps, gaming) are trees of modules whose data sources are relative URLs on the same
service; each resolves to a list of product cards. Product pages, search and the Windows Update
category used for package delivery come from the same place. No account is needed to browse.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field

from . import net, settings

EDGE = "https://storeedgefd.dsx.mp.microsoft.com/v9.0"


def _params(**extra: str) -> str:
    cfg = settings.load()
    query = {"market": cfg["market"], "locale": cfg["locale"], "deviceFamily": "Windows.Desktop"}
    query.update({k: v for k, v in extra.items() if v is not None})
    return urllib.parse.urlencode(query)


@dataclass
class Image:
    kind: str
    url: str
    width: int
    height: int
    background: str = ""

    @property
    def full_url(self) -> str:
        return ("https:" + self.url) if self.url.startswith("//") else self.url


def _images(raw: list | None) -> list[Image]:
    out = []
    for item in raw or []:
        url = item.get("Url") or ""
        if not url:
            continue
        out.append(Image(item.get("ImageType") or "", url, int(item.get("Width") or 0),
                         int(item.get("Height") or 0), item.get("BackgroundColor") or ""))
    return out


def pick_image(images: list[Image], prefer: tuple[str, ...], min_width: int = 0) -> Image | None:
    for kind in prefer:
        matches = [i for i in images if i.kind.lower() == kind.lower()]
        if matches:
            big = [i for i in matches if i.width >= min_width] or matches
            return sorted(big, key=lambda i: i.width)[0] if min_width else matches[0]
    return images[0] if images else None


def image_url(image: Image | None, width: int) -> str | None:
    """A resized rendition; the image service scales on request."""
    if image is None:
        return None
    url = image.full_url
    if "store-images.s-microsoft.com" in url and "?" not in url:
        return f"{url}?w={width}&mode=scale&q=90&format=jpg" if image.kind.lower() not in ("logo", "tile") \
            else f"{url}?w={width}&mode=scale"
    return url


@dataclass
class Card:
    product_id: str
    title: str
    price: str
    rating: float
    rating_count: str
    kind: str                     # "app", "game", "collection", ...
    images: list[Image]
    description: str = ""
    publisher: str = ""
    category: str = ""
    family_names: list[str] = field(default_factory=list)
    installer: str = ""           # WindowsUpdate (MSIX via FE3), WPM/Win32 (classic installer), ...

    @property
    def is_product(self) -> bool:
        return bool(re.fullmatch(r"[0-9A-Z]{12}", self.product_id))

    @property
    def is_game(self) -> bool:
        return self.category.lower() == "games" or self.kind == "game"

    @classmethod
    def from_json(cls, raw: dict) -> "Card":
        installer = raw.get("Installer") or {}
        return cls(
            product_id=raw.get("ProductId") or "",
            title=raw.get("Title") or "",
            price=raw.get("DisplayPrice") or ("Free" if raw.get("Price") == 0 else ""),
            rating=float(raw.get("AverageRating") or 0),
            rating_count=str(raw.get("RatingsCount") or raw.get("RatingCount") or ""),
            kind=raw.get("TypeTag") or "",
            images=_images(raw.get("Images")),
            description=raw.get("Description") or raw.get("ShortDescription") or "",
            publisher=raw.get("PublisherName") or "",
            category=raw.get("ProductFamilyName") or "",
            family_names=[f for f in raw.get("PackageFamilyNames") or [] if f],
            installer=installer.get("Type") or "",
        )


@dataclass
class Section:
    title: str
    layout: str                   # "hero", "row", "grid"
    source: str                   # relative URI on EDGE
    cards: list[Card] = field(default_factory=list)


@dataclass
class Sku:
    sku_id: str
    title: str
    price: str
    category_id: str              # WuCategoryId, for package delivery
    family_name: str
    architectures: list[str]


@dataclass
class Product:
    product_id: str
    title: str
    publisher: str
    description: str
    short_description: str
    price: str
    rating: float
    rating_count: int
    category: str
    images: list[Image]
    screenshots: list[Image]
    skus: list[Sku]
    family_names: list[str]
    capabilities: list[str]
    permissions: list[str]
    age_rating: str
    installer: str
    installer_id: str
    is_msixvc: bool
    platforms: list[str]
    allowed_platforms: list[str]
    system_requirements: dict
    raw: dict

    @property
    def is_game(self) -> bool:
        return self.category.lower() == "games"

    @property
    def category_id(self) -> str:
        for sku in self.skus:
            if sku.category_id:
                return sku.category_id
        return ""

    @property
    def is_free(self) -> bool:
        return self.price.lower() in ("free", "free+", "$0.00", "")

    @property
    def bundled_ids(self) -> list[str]:
        """Products a bundle includes (Minecraft: Java & Bedrock -> the Launcher and Bedrock)."""
        out: list[str] = []
        for sku in self.raw.get("Skus") or []:
            for pid in sku.get("BundledSkus") or []:
                if isinstance(pid, str) and pid not in out and pid != self.product_id:
                    out.append(pid)
        return out

    @property
    def runs_on_desktop(self) -> bool:
        return not self.allowed_platforms or any(p in ("Windows.Desktop", "Windows.Universal") for p in self.allowed_platforms)


# ---- pages -------------------------------------------------------------------------------------

PAGES = {
    "home": "Home",
    "apps": "Apps",
    "gaming": "Gaming",
}

# Placeholders the Store fills from the user's profile; without one the service uses its defaults.
_PLACEHOLDERS = re.compile(r"&?[A-Za-z]+=\[[A-Z_]+\]")
_LAYOUTS = {
    "CoreUi-MosaicHero": "hero",
    "onestore-trendingcollection": "row",
    "coreui-productplacement": "row",
    "CoreUI-ProductPlacement": "row",
    "CoreUI-FeatureCollection": "row",
    "coreui-InlinePlacement": "row",
}


def _module_title(module: dict, overrides: dict[str, str]) -> str:
    if module.get("Id") + "_Override" in overrides:
        return overrides[module["Id"] + "_Override"]
    fields = module.get("Fields") or {}
    for key in ("title", "Title", "heading"):
        if isinstance(fields.get(key), str) and fields[key].strip():
            return fields[key]
    return ""


def page_sections(page: str = "home") -> list[Section]:
    """The page's module tree flattened into ordered sections, each with an unresolved source."""
    data = net.get_json(f"{EDGE}/pages/{page}?{_params()}", max_age=3600)
    item = data[0] if isinstance(data, list) else data
    payload = item.get("Payload") or {}
    overrides = {}
    for entry in payload.get("CmsData") or []:
        for key in entry.get("Key") or []:
            for value in entry.get("Value") or []:
                if value.get("Title"):
                    overrides[key] = value["Title"]
    sections: list[Section] = []
    seen: set[str] = set()

    def walk(module: dict) -> None:
        kind = module.get("ModuleDefinitionName") or ""
        for source in module.get("DataSources") or []:
            uri = ((source.get("DataBinder") or {}).get("Uri")) or ""
            if not uri or kind not in _LAYOUTS:
                continue
            uri = _PLACEHOLDERS.sub("", uri)
            if uri in seen:
                continue
            seen.add(uri)
            sections.append(Section(_module_title(module, overrides), _LAYOUTS[kind], uri))
        for child in module.get("Modules") or []:
            walk(child)

    root = ((payload.get("PageObject") or {}).get("RootModule")) or {}
    walk(root)
    return sections


def load_section(section: Section) -> Section:
    data = net.get_json(EDGE + section.source, max_age=3600)
    item = data[0] if isinstance(data, list) else data
    payload = item.get("Payload") or {}
    section.title = section.title or payload.get("Title") or payload.get("CuratedTitle") or ""
    section.cards = [Card.from_json(c) for c in payload.get("Cards") or []]
    return section


def collection(name: str, media: str, size: int = 30) -> Section:
    """A named algorithmic list: TopFree, TopGrossing, toptrending, ... for Apps or Games."""
    source = f"/recommendations/collections/{name}?{_params(mediaType=media, pageSize=str(size), cardsEnabled='true')}"
    return load_section(Section("", "grid", source))


# ---- search ------------------------------------------------------------------------------------

def search(query: str, media: str | None = None) -> list[Card]:
    data = net.get_json(f"{EDGE}/search?{_params(query=query, mediaType=media)}", max_age=600)
    payload = (data[0] if isinstance(data, list) else data).get("Payload") or {}
    return [Card.from_json(c) for c in payload.get("SearchResults") or []]


def suggest(query: str) -> list[str]:
    try:
        data = net.get_json(f"{EDGE}/autosuggest?{_params(query=query)}", max_age=600)
    except (net.NetError, ValueError):
        return []
    payload = (data[0] if isinstance(data, list) else data).get("Payload") or {}
    out = []
    for group in payload.get("AssetSuggestions") or payload.get("SearchSuggestions") or []:
        title = group.get("Title") or group.get("DisplayText")
        if title:
            out.append(title)
    return out[:8]


# ---- products ----------------------------------------------------------------------------------

def product(product_id: str) -> Product:
    data = net.get_json(f"{EDGE}/products/{urllib.parse.quote(product_id)}?{_params()}", max_age=1800)
    raw = (data[0] if isinstance(data, list) else data).get("Payload") or {}
    if not raw.get("ProductId"):
        raise net.NetError(f"No product {product_id}")
    skus = []
    for sku in raw.get("Skus") or []:
        try:
            fulfillment = json.loads(sku.get("FulfillmentData") or "{}")
        except ValueError:
            fulfillment = {}
        arches: list[str] = []
        for requirement in sku.get("PackageRequirements") or []:
            for arch in requirement.get("SupportedArchitectures") or []:
                if arch not in arches:
                    arches.append(arch)
        skus.append(Sku(sku.get("SkuId") or "", sku.get("SkuTitle") or "", sku.get("DisplayPrice") or "",
                        fulfillment.get("WuCategoryId") or "", fulfillment.get("PackageFamilyName") or "", arches))
    images = _images(raw.get("Images"))
    rating = (raw.get("ProductRatings") or [{}])[0]
    installer = raw.get("Installer") or {}
    return Product(
        product_id=raw["ProductId"],
        title=raw.get("Title") or "",
        publisher=raw.get("PublisherName") or raw.get("DeveloperName") or "",
        description=raw.get("Description") or "",
        short_description=raw.get("ShortDescription") or "",
        price=raw.get("DisplayPrice") or "",
        rating=float(raw.get("AverageRating") or 0),
        rating_count=int(raw.get("RatingCount") or 0),
        category=", ".join(raw.get("Categories") or []) or raw.get("ProductFamilyName") or "",
        images=images,
        screenshots=[i for i in images if i.kind.lower() == "screenshot"],
        skus=skus,
        family_names=[f for f in raw.get("PackageFamilyNames") or [] if f],
        capabilities=list(raw.get("PackageAndDeviceCapabilities") or []),
        permissions=list(raw.get("PermissionsRequired") or []),
        age_rating=rating.get("LongName") or rating.get("RatingValue") or "",
        installer=installer.get("Type") or "",
        installer_id=installer.get("Id") or "",
        is_msixvc=bool(raw.get("IsMsixvc")),
        platforms=list(raw.get("Platforms") or []),
        allowed_platforms=list(raw.get("AllowedPlatforms") or []),
        system_requirements=raw.get("SystemRequirements") or {},
        raw=raw,
    )


@dataclass
class Win32Installer:
    """A classic (non-MSIX) Store listing: the publisher's own installer, which plain Wine/Proton runs."""
    url: str
    sha256: str
    kind: str                     # exe, msi, inno, nullsoft, ...
    arch: str
    switches: str


def win32_installers(product_id: str) -> list[Win32Installer]:
    data = net.get_json(f"{EDGE}/packageManifests/{urllib.parse.quote(product_id)}?{_params()}", max_age=1800)
    out = []
    for version in ((data.get("Data") or {}).get("Versions") or []):
        for inst in version.get("Installers") or []:
            if not inst.get("InstallerUrl"):
                continue
            switches = (inst.get("InstallerSwitches") or {}).get("Silent") or ""
            out.append(Win32Installer(inst["InstallerUrl"], inst.get("InstallerSha256") or "",
                                      inst.get("InstallerType") or "exe", inst.get("Architecture") or "x64", switches))
    return out
