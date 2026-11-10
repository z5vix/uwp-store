"""AppX/MSIX reading: manifests, bundle members and framework dependencies. Pure Python, no Wine."""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

ARCH_PREFERENCE = ("x64", "neutral", "x86")


class PackageError(Exception):
    pass


@dataclass
class Dependency:
    name: str
    min_version: str
    publisher: str


@dataclass
class Manifest:
    name: str
    publisher: str
    version: str
    arch: str
    display_name: str
    is_framework: bool
    dependencies: list[Dependency] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    full_trust: bool = False


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_manifest(data: bytes) -> Manifest:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise PackageError(f"Unreadable AppxManifest.xml: {exc}") from exc
    identity = next((e for e in root.iter() if _local(e.tag) == "Identity"), None)
    if identity is None:
        raise PackageError("AppxManifest.xml has no Identity")
    display = ""
    framework = False
    for element in root.iter():
        tag = _local(element.tag)
        if tag == "DisplayName" and not display and element.text:
            display = element.text.strip()
        elif tag == "Framework" and (element.text or "").strip().lower() == "true":
            framework = True
    deps = [Dependency(e.get("Name", ""), e.get("MinVersion", "0.0.0.0"), e.get("Publisher", ""))
            for e in root.iter() if _local(e.tag) == "PackageDependency"]
    families = [e.get("Name", "") for e in root.iter() if _local(e.tag) == "TargetDeviceFamily"]
    full_trust = any(_local(e.tag) == "Capability" and e.get("Name") == "runFullTrust" for e in root.iter())
    return Manifest(identity.get("Name", ""), identity.get("Publisher", ""), identity.get("Version", ""),
                    (identity.get("ProcessorArchitecture") or "neutral").lower(), display, framework, deps,
                    families, full_trust)


def _manifest_from_zip(archive: zipfile.ZipFile) -> bytes | None:
    for name in archive.namelist():
        if name.lower() == "appxmanifest.xml":
            return archive.read(name)
    return None


def bundle_members(archive: zipfile.ZipFile) -> list[str]:
    return [n for n in archive.namelist() if n.lower().endswith((".appx", ".msix")) and "/" not in n.strip("/")]


def _member_arch(name: str) -> str:
    low = name.lower()
    for arch in ("x64", "arm64", "x86", "arm", "neutral"):
        if re.search(rf"[_.-]{arch}[_.-]", low) or low.endswith(f"_{arch}.appx") or low.endswith(f"_{arch}.msix"):
            return arch
    return "neutral"


def read_manifest(path: Path) -> Manifest:
    """The manifest that will be installed: the package's own, or the best x64 member's for a bundle."""
    if not zipfile.is_zipfile(path):
        raise PackageError(f"{path.name} is not an AppX/MSIX package (encrypted packages are not supported)")
    with zipfile.ZipFile(path) as archive:
        data = _manifest_from_zip(archive)
        if data:
            return parse_manifest(data)
        members = [m for m in bundle_members(archive) if not re.search(r"(scale|language|lang)-", m, re.I)]
        if not members:
            raise PackageError(f"{path.name} has no manifest and no application package")
        members.sort(key=lambda m: ARCH_PREFERENCE.index(_member_arch(m)) if _member_arch(m) in ARCH_PREFERENCE else 9)
        best = members[0]
        if _member_arch(best) not in ARCH_PREFERENCE:
            raise PackageError(f"{path.name} has no x64, x86 or neutral package (Arm only)")
        with archive.open(best) as handle:
            inner = zipfile.ZipFile(io.BytesIO(handle.read()))
        data = _manifest_from_zip(inner)
        if not data:
            raise PackageError(f"{best} has no AppxManifest.xml")
        return parse_manifest(data)


def version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for piece in (version or "0").split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple((parts + [0, 0, 0, 0])[:4])


def installed_framework(dirs: list[Path], dep: Dependency, arch: str = "x64") -> str | None:
    """Name of a framework directory NAME_VERSION_ARCH__PUBLISHERID satisfying dep, if one exists."""
    best = None
    best_version: tuple[int, ...] = ()
    for folder in dirs:
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            match = re.fullmatch(rf"{re.escape(dep.name)}_([\d.]+)_{arch}__([a-z0-9]{{13}})", entry.name, re.I)
            if not match or not entry.is_dir():
                continue
            version = version_tuple(match.group(1))
            if version >= version_tuple(dep.min_version) and version > best_version:
                best, best_version = entry.name, version
    return best


def extract_package(path: Path, destination: Path) -> Path:
    """Unpack a (framework) package to a directory, rejecting unsafe member names."""
    tmp = destination.with_name(destination.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            name = PurePosixPath(member.filename)
            if name.is_absolute() or ".." in name.parts:
                raise PackageError(f"Unsafe path in {path.name}: {member.filename}")
            target = tmp.joinpath(*[p for p in name.parts if p])
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    shutil.rmtree(destination, ignore_errors=True)
    tmp.replace(destination)
    return destination
