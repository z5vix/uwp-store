"""Microsoft Store package delivery (FE3) client.

The Store resolves a product's packages through the Windows Update delivery service:

  1. GetCookie            -> an opaque session cookie
  2. SyncUpdates          -> the update entries for the product's WuCategoryId: one per package
                             (the app, every architecture, and the frameworks it depends on)
  3. GetExtendedUpdateInfo2 -> short-lived signed download URLs for one update's files

Free products resolve anonymously. Products that need a licence (paid, Game Pass, some
preinstalled titles) answer with no downloadable files unless the request carries the user's
Microsoft account ticket, which this client accepts through ``msa_token`` but never invents.
Nothing here decrypts or bypasses licensing; encrypted packages (.eappx/.emsix) are reported as such.
"""

from __future__ import annotations

import datetime as _dt
import html
import re
import uuid
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from . import net

FE3 = "https://fe3.delivery.mp.microsoft.com/ClientWebService/client.asmx"
FE3_SECURED = FE3 + "/secured"
WU_NS = "http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService"

# The update tree's root nodes a current Windows 11 client reports as installed. The service uses
# them to decide applicability; the list is the one Store clients send.
_INSTALLED_NON_LEAF = (
    1, 2, 3, 11, 19, 544, 549, 2359974, 2359977, 5169044, 8788830, 23110993, 23110994, 54341900,
    54343656, 59830006, 59830007, 59830008, 60484010, 62450018, 62450019, 62450020, 66027979,
    66053150, 97657898, 98822896, 98959022, 98959023, 98959024, 98959025, 98959026, 104433538,
    104900364, 105489019, 117765322, 129905029, 130040031, 132387090, 132393049, 133399034,
    138537048, 140377312, 143747671, 158941041, 158941042, 158941043, 158941044, 159123858,
    159130928, 164836897, 164847386, 164848327, 164852241, 164852246, 164852252, 164852253,
)

_DEVICE_ATTRIBUTES = "&amp;".join((
    "E:BranchReadinessLevel=CB", "CurrentBranch=ge_release", "OEMModel=Linux", "FlightRing=Retail",
    "AttrDataVer=21", "InstallLanguage=en-US", "OSUILocale=en-US", "InstallationType=Client",
    "FlightingBranchName=", "OSSkuId=48", "FlightContent=Branch", "App=WU", "AppVer=10.0.26100.1",
    "OSArchitecture=AMD64", "UpdateManagementGroup=2", "IsFlightingEnabled=0",
    "IsDeviceRetailDemo=0", "TelemetryLevel=1", "OSVersion=10.0.26100.1",
    "DeviceFamily=Windows.Desktop",
))


class DeliveryError(Exception):
    pass


@dataclass
class PackageFile:
    """One downloadable package the service offers for a product."""
    moniker: str            # Name_Version_Arch_ResourceId_PublisherId, the package full name
    name: str               # package identity name
    version: str
    arch: str               # x64, x86, arm64, arm, neutral
    publisher_id: str
    file_name: str          # name of the file on the CDN, with its real extension
    size: int
    digest: str             # base64 SHA-1 (or SHA-256) as the service reports it
    digest_alg: str
    update_id: str
    revision: str
    is_framework: bool = False
    is_bundle: bool = False
    is_encrypted: bool = False
    url: str | None = None

    @property
    def family_name(self) -> str:
        return f"{self.name}_{self.publisher_id}"

    @property
    def extension(self) -> str:
        match = re.search(r"\.(e?appx(?:bundle)?|e?msix(?:bundle)?|appxvc|msixvc)$", self.file_name, re.I)
        return match.group(1).lower() if match else ""

    @property
    def local_name(self) -> str:
        return f"{self.moniker}.{self.extension or 'appx'}"


@dataclass
class Resolution:
    category_id: str
    files: list[PackageFile] = field(default_factory=list)
    needs_license: bool = False


def _now(offset_minutes: int = 0) -> str:
    moment = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=offset_minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _envelope(action: str, to: str, body: str, msa_token: str | None) -> bytes:
    ticket = (
        '<TicketType Name="MSA" Version="1.0" Policy="MBI_SSL">'
        f"<User>{html.escape(msa_token)}</User></TicketType>"
        if msa_token else
        '<TicketType Name="AAD" Version="1.0" Policy="MBI_SSL"></TicketType>'
    )
    return (
        '<s:Envelope xmlns:a="http://www.w3.org/2005/08/addressing" '
        'xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header>'
        f'<a:Action s:mustUnderstand="1">http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService/{action}</a:Action>'
        f"<a:MessageID>urn:uuid:{uuid.uuid4()}</a:MessageID>"
        f'<a:To s:mustUnderstand="1">{to}</a:To>'
        '<o:Security s:mustUnderstand="1" xmlns:o="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        '<Timestamp xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f"<Created>{_now()}</Created><Expires>{_now(5)}</Expires></Timestamp>"
        '<wuws:WindowsUpdateTicketsToken wsu:id="ClientMSA" '
        'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" '
        'xmlns:wuws="http://schemas.microsoft.com/msus/2014/10/WindowsUpdateAuthorization">'
        f"{ticket}</wuws:WindowsUpdateTicketsToken></o:Security></s:Header>"
        f"<s:Body>{body}</s:Body></s:Envelope>"
    ).encode()


def _post(url: str, envelope: bytes) -> ET.Element:
    status, data = net.post(url, envelope, {"Content-Type": "application/soap+xml; charset=utf-8"}, timeout=60)
    if status != 200:
        fault = re.search(rb"<(?:\w+:)?(?:Text|faultstring|ErrorCode)[^>]*>([^<]+)<", data or b"")
        detail = fault.group(1).decode(errors="replace") if fault else f"HTTP {status}"
        raise DeliveryError(f"Delivery service refused the request: {detail}")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise DeliveryError(f"Delivery service sent unreadable XML: {exc}") from exc


def _find_text(root: ET.Element, local: str) -> str | None:
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == local:
            return element.text
    return None


def _children(root: ET.Element, local: str) -> list[ET.Element]:
    return [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == local]


def _child(element: ET.Element, local: str) -> ET.Element | None:
    for child in element:
        if child.tag.rsplit("}", 1)[-1] == local:
            return child
    return None


_MONIKER = re.compile(r"^(?P<name>[^_]+)_(?P<version>[\d.]+)_(?P<arch>[^_]+)_(?P<res>[^_]*)_(?P<pub>[a-z0-9]{13})$", re.I)


def parse_moniker(moniker: str) -> tuple[str, str, str, str]:
    match = _MONIKER.match(moniker)
    if not match:
        return moniker, "", "neutral", ""
    return match["name"], match["version"], match["arch"].lower(), match["pub"]


class DeliveryClient:
    def __init__(self, msa_token: str | None = None) -> None:
        self.msa_token = msa_token
        self._cookie: str | None = None

    def _get_cookie(self) -> str:
        if self._cookie:
            return self._cookie
        body = (
            f'<GetCookie xmlns="{WU_NS}"><oldCookie><Expiration>2016-07-27T07:18:09Z</Expiration></oldCookie>'
            f"<lastChange>2015-10-21T17:01:07.1472913Z</lastChange><currentTime>{_now()}</currentTime>"
            "<protocolVersion>1.40</protocolVersion></GetCookie>"
        )
        root = _post(FE3, _envelope("GetCookie", FE3, body, self.msa_token))
        cookie = _find_text(root, "EncryptedData")
        if not cookie:
            raise DeliveryError("Delivery service returned no session cookie")
        self._cookie = cookie
        return cookie

    def resolve(self, category_id: str, *, ring: str = "Retail") -> Resolution:
        """List every package the service offers for a product's WuCategoryId.

        A signed-in ticket the service refuses never blocks free products: the lookup is retried anonymously.
        """
        try:
            return self._resolve(category_id, ring)
        except DeliveryError:
            if not self.msa_token:
                raise
            self.msa_token, self._cookie = None, None
            return self._resolve(category_id, ring)

    def _resolve(self, category_id: str, ring: str) -> Resolution:
        installed = "".join(f"<int>{n}</int>" for n in _INSTALLED_NON_LEAF)
        attributes = _DEVICE_ATTRIBUTES.replace("FlightRing=Retail", f"FlightRing={ring}")
        body = (
            f'<SyncUpdates xmlns="{WU_NS}"><cookie><Expiration>2045-03-11T02:02:48Z</Expiration>'
            f"<EncryptedData>{self._get_cookie()}</EncryptedData></cookie><parameters>"
            "<ExpressQuery>false</ExpressQuery>"
            f"<InstalledNonLeafUpdateIDs>{installed}</InstalledNonLeafUpdateIDs>"
            "<OtherCachedUpdateIDs/><SkipSoftwareSync>false</SkipSoftwareSync>"
            "<NeedTwoGroupOutOfScopeUpdates>true</NeedTwoGroupOutOfScopeUpdates>"
            f"<FilterAppCategoryIds><CategoryIdentifier><Id>{html.escape(category_id)}</Id></CategoryIdentifier></FilterAppCategoryIds>"
            "<TreatAppCategoryIdsAsInstalled>true</TreatAppCategoryIdsAsInstalled>"
            "<AlsoPerformRegularSync>false</AlsoPerformRegularSync><ComputerSpec/>"
            "<ExtendedUpdateInfoParameters><XmlUpdateFragmentTypes>"
            "<XmlUpdateFragmentType>Extended</XmlUpdateFragmentType>"
            "<XmlUpdateFragmentType>LocalizedProperties</XmlUpdateFragmentType>"
            "<XmlUpdateFragmentType>Eula</XmlUpdateFragmentType>"
            "</XmlUpdateFragmentTypes><Locales><string>en-US</string><string>en</string></Locales>"
            "</ExtendedUpdateInfoParameters>"
            "<ClientPreferredLanguages><string>en-US</string></ClientPreferredLanguages>"
            "<ProductsParameters><SyncCurrentVersionOnly>false</SyncCurrentVersionOnly>"
            f"<DeviceAttributes>{attributes}</DeviceAttributes>"
            "<CallerAttributes>Interactive=1;IsSeeker=0;</CallerAttributes><Products/>"
            "</ProductsParameters></parameters></SyncUpdates>"
        )
        root = _post(FE3, _envelope("SyncUpdates", FE3, body, self.msa_token))

        # ExtendedUpdateInfo carries the file list per update (keyed by the numeric update ID);
        # NewUpdates carries the identity (GUID + revision) and whether files are downloadable.
        files_by_id: dict[str, list[ET.Element]] = {}
        for update in _children(root, "Update"):
            uid = _child(update, "ID")
            xml = _child(update, "Xml")
            if uid is None or xml is None or not xml.text:
                continue
            fragment = ET.fromstring(f"<r>{xml.text}</r>")
            found = [f for f in fragment.iter("File") if f.get("InstallerSpecificIdentifier")]
            if found:
                files_by_id[uid.text or ""] = found

        resolution = Resolution(category_id)
        for info in _children(root, "UpdateInfo"):
            uid = _child(info, "ID")
            xml = _child(info, "Xml")
            if uid is None or xml is None or not xml.text:
                continue
            fragment = ET.fromstring(f"<r>{xml.text}</r>")
            identity = fragment.find(".//UpdateIdentity")
            if identity is None:
                continue
            secured = fragment.find(".//SecuredFragment") is not None
            framework = 'IsAppxFramework="true"' in xml.text
            for element in files_by_id.get(uid.text or "", []):
                moniker = element.get("InstallerSpecificIdentifier", "")
                file_name = element.get("FileName", "")
                name, version, arch, publisher = parse_moniker(moniker)
                package = PackageFile(
                    moniker=moniker, name=name, version=version, arch=arch, publisher_id=publisher,
                    file_name=file_name, size=int(element.get("Size", "0") or 0),
                    digest=element.get("Digest", ""), digest_alg=element.get("DigestAlgorithm", "SHA1"),
                    update_id=identity.get("UpdateID", ""), revision=identity.get("RevisionNumber", "1"),
                    is_framework=framework,
                    is_bundle=bool(re.search(r"bundle$", file_name, re.I)),
                    is_encrypted=bool(re.search(r"\.e(appx|msix)", file_name, re.I)),
                )
                if secured:
                    resolution.files.append(package)
                else:
                    resolution.needs_license = True
        # one entry per package full name; the service lists some twice (once per revision)
        unique: dict[str, PackageFile] = {}
        for package in resolution.files:
            current = unique.get(package.moniker)
            if current is None or int(package.revision or 0) > int(current.revision or 0):
                unique[package.moniker] = package
        resolution.files = sorted(unique.values(), key=lambda p: (p.is_framework, p.name.lower(), p.arch))
        return resolution

    def file_urls(self, package: PackageFile) -> list[tuple[str, str]]:
        """Signed download URLs for one update: (url, digest) pairs, valid for a few minutes."""
        body = (
            f'<GetExtendedUpdateInfo2 xmlns="{WU_NS}"><updateIDs><UpdateIdentity>'
            f"<UpdateID>{package.update_id}</UpdateID><RevisionNumber>{package.revision}</RevisionNumber>"
            "</UpdateIdentity></updateIDs><infoTypes>"
            "<XmlUpdateFragmentType>FileUrl</XmlUpdateFragmentType>"
            "<XmlUpdateFragmentType>FileDecryption</XmlUpdateFragmentType>"
            f"</infoTypes><deviceAttributes>{_DEVICE_ATTRIBUTES}</deviceAttributes></GetExtendedUpdateInfo2>"
        )
        root = _post(FE3_SECURED, _envelope("GetExtendedUpdateInfo2", FE3_SECURED, body, self.msa_token))
        out = []
        for location in _children(root, "FileLocation"):
            url = _child(location, "Url")
            digest = _child(location, "FileDigest")
            if url is not None and url.text:
                out.append((url.text, digest.text if digest is not None and digest.text else ""))
        return out

    def url_for(self, package: PackageFile) -> str:
        urls = self.file_urls(package)
        for url, digest in urls:
            if digest and digest == package.digest:
                return url
        # the blockmap shares the update; the package itself is the larger, non-blockmap URL
        for url, _digest in urls:
            if "blockmap" not in url.lower():
                return url
        raise DeliveryError(f"No download location for {package.moniker}")
