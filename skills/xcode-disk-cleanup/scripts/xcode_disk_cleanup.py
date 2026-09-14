#!/usr/bin/env python3
"""Audit Xcode-related disk usage and apply only explicitly approved cleanups."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
CONFIRMATION_PHRASE = "I_APPROVED_THE_SELECTED_ITEMS"
IRREVERSIBLE_PHRASE = "I_APPROVED_IRREVERSIBLE_SIMULATOR_DELETION"
XCODE_INSTALLER_PATTERN = re.compile(
    r"(?i)\bXcode(?:[_\s-]+)?(?P<version>\d+(?:\.\d+){0,2})?.*\.(?:dmg|xip|zip)$"
)
SKIP_SCAN_NAMES = {
    ".git",
    ".svn",
    "node_modules",
    "SourcePackages",
    "checkouts",
    "Pods",
    "Carthage",
}


@dataclasses.dataclass
class Candidate:
    id: str
    category: str
    label: str
    path: str | None
    bytes: int
    risk: str
    action: str
    reason: str
    evidence: list[str]
    regeneration_cost: str
    identifier: str | None = None
    device_set: str | None = None
    fingerprint: dict[str, int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def run(
    command: list[str],
    *,
    check: bool = False,
    timeout: int = 30,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
        env=env,
    )


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    # `du` exits non-zero on permission errors but still prints a usable
    # total for the readable portion, so accept any parsable output.
    result = run(["/usr/bin/du", "-sk", str(path)], timeout=180)
    if not result.stdout.strip():
        return 0
    try:
        return int(result.stdout.split()[0]) * 1024
    except ValueError:
        return 0


def fingerprint(path: Path, size: int | None = None) -> dict[str, int] | None:
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return None
    return {
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "modified_ns": stat.st_mtime_ns,
        "bytes": size if size is not None else directory_size(path),
    }


def gibibytes(value: int) -> str:
    return f"{value / (1024 ** 3):.2f} GiB"


def days_unchanged(path: Path, reference: dt.datetime | None = None) -> int | None:
    """Whole days since the given timestamp, or the path's own mtime."""
    moment = reference
    if moment is None:
        try:
            moment = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        except OSError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return max(0, (dt.datetime.now(dt.timezone.utc) - moment).days)


def safe_plist(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            value = plistlib.load(stream)
            return value if isinstance(value, dict) else {}
    except (FileNotFoundError, plistlib.InvalidFileException, PermissionError, OSError):
        return {}


def candidate_for_path(
    *,
    candidate_id: str,
    category: str,
    label: str,
    path: Path,
    risk: str,
    action: str,
    reason: str,
    evidence: Iterable[str],
    regeneration_cost: str,
) -> Candidate:
    size = directory_size(path)
    return Candidate(
        id=candidate_id,
        category=category,
        label=label,
        path=str(path.resolve(strict=False)),
        bytes=size,
        risk=risk,
        action=action,
        reason=reason,
        evidence=list(evidence),
        regeneration_cost=regeneration_cost,
        fingerprint=fingerprint(path, size),
    )


def inspect_derived_data(home: Path) -> list[Candidate]:
    root = home / "Library/Developer/Xcode/DerivedData"
    candidates: list[Candidate] = []
    if not root.is_dir():
        return candidates

    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        metadata = safe_plist(entry / "info.plist")
        workspace = metadata.get("WorkspacePath")
        last_accessed = metadata.get("LastAccessedDate")
        evidence = []
        age = days_unchanged(
            entry,
            last_accessed if isinstance(last_accessed, dt.datetime) else None,
        )
        if last_accessed:
            evidence.append(f"Last accessed: {last_accessed}")
        if age is not None:
            evidence.append(f"Unchanged for {age} days.")

        if isinstance(workspace, str):
            workspace_path = Path(workspace).expanduser()
            evidence.append(f"Recorded workspace: {workspace}")
            if not workspace_path.exists():
                candidates.append(
                    candidate_for_path(
                        candidate_id=f"derived-missing:{entry.name}",
                        category="DerivedData",
                        label=f"DerivedData for missing workspace: {entry.name}",
                        path=entry,
                        risk="regenerable",
                        action="trash-path",
                        reason="The recorded workspace no longer exists.",
                        evidence=evidence,
                        regeneration_cost="Rebuild and package resolution if the project returns.",
                    )
                )
                continue

        if not metadata and entry.name not in {
            "ModuleCache.noindex",
            "SDKExplicitPrecompiledModules",
            "SDKStatCaches.noindex",
            "SymbolCache.noindex",
            "CompilationCache.noindex",
        }:
            children = {child.name for child in entry.iterdir()}
            if children and children.issubset({"SourcePackages", "Logs"}):
                package_age = days_unchanged(entry)
                package_evidence = [f"Contents: {', '.join(sorted(children))}"]
                if package_age is not None:
                    package_evidence.append(f"Unchanged for {package_age} days.")
                candidates.append(
                    candidate_for_path(
                        candidate_id=f"derived-packages-only:{entry.name}",
                        category="DerivedData",
                        label=f"Metadata-free package cache: {entry.name}",
                        path=entry,
                        risk="regenerable",
                        action="trash-path",
                        reason="No Xcode workspace metadata or build products were found.",
                        evidence=package_evidence,
                        regeneration_cost="Dependencies must be resolved or downloaded again.",
                    )
                )

    return candidates


def inspect_documentation(home: Path) -> list[Candidate]:
    root = home / "Library/Developer/Xcode/DocumentationCache"
    if not root.is_dir():
        return []
    versions = sorted(
        (entry for entry in root.iterdir() if entry.is_dir()),
        key=lambda item: item.name,
    )
    if len(versions) < 2:
        return []
    latest = versions[-1]
    return [
        candidate_for_path(
            candidate_id=f"documentation:{entry.name}",
            category="Documentation cache",
            label=f"Older documentation cache {entry.name}",
            path=entry,
            risk="regenerable",
            action="trash-path",
            reason=(
                f"A newer cache ({latest.name}) exists. The same documentation stays "
                "available; only the outdated unused copy is removed."
            ),
            evidence=[f"Newest sibling cache: {latest.name}"],
            regeneration_cost="None for current documentation; only outdated unused caches are removed.",
        )
        for entry in versions[:-1]
    ]


def simctl_json(arguments: list[str], *, device_set: str | None = None) -> dict[str, Any]:
    command = ["/usr/bin/xcrun", "simctl"]
    if device_set:
        command.extend(["--set", device_set])
    result = run([*command, *arguments], timeout=60)
    if result.returncode != 0:
        return {}
    try:
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def inspect_simulators(home: Path) -> list[Candidate]:
    candidates: list[Candidate] = []
    for device_set in (None, "previews"):
        payload = simctl_json(
            ["list", "--json", "devices"],
            device_set=device_set,
        )
        devices = payload.get("devices", {})
        if not isinstance(devices, dict):
            continue
        device_root = home / "Library/Developer/CoreSimulator/Devices"
        for runtime, entries in devices.items():
            if not isinstance(entries, list):
                continue
            for item in entries:
                if not isinstance(item, dict) or item.get("isAvailable", True):
                    continue
                udid = item.get("udid")
                if not isinstance(udid, str):
                    continue
                reported_path = item.get("dataPath")
                path = (
                    Path(reported_path)
                    if isinstance(reported_path, str)
                    else device_root / udid
                )
                size = directory_size(path)
                set_label = "SwiftUI Previews" if device_set else "default"
                candidates.append(
                    Candidate(
                        id=f"simulator-unavailable:{device_set or 'default'}:{udid}",
                        category="Unavailable simulator",
                        label=f"{item.get('name', 'Simulator')} ({runtime.rsplit('.', 1)[-1]})",
                        path=str(path.resolve(strict=False)),
                        bytes=size,
                        risk="destructive",
                        action="simctl-delete-device",
                        reason="The device runtime is unavailable, so this simulator cannot boot.",
                        evidence=[
                            f"UDID: {udid}",
                            f"Device set: {set_label}",
                            f"State: {item.get('state', 'Unknown')}",
                            f"Runtime: {runtime}",
                        ],
                        regeneration_cost="Installed apps, app data, keychain, and settings are permanently lost.",
                        identifier=udid,
                        device_set=device_set,
                        fingerprint=fingerprint(path, size),
                    )
                )
    return candidates


def runtime_inventory() -> list[dict[str, Any]]:
    """Installed runtimes from `simctl runtime list -j`, keyed entries flattened."""
    payload = simctl_json(["runtime", "list", "-j"])
    runtimes: list[dict[str, Any]] = []
    for uuid, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        entry.setdefault("identifier", uuid)
        runtimes.append(entry)
    return runtimes


CHOSEN_RUNTIME_LINE = re.compile(r"Chosen Runtime:\s*(.+)")
CHOSEN_RUNTIME_BUILD = re.compile(r"\([\d.]+\s*-\s*(\S+)\)")
SDK_VERSION_LINE = re.compile(r"SDK Version:\s*(\S+)")
PLATFORM_LINE = re.compile(r"Platform:\s*(\S+)")


def platform_fragment(identifier: str) -> str:
    """Normalize SDK and simulator platform identifiers to a comparable stem."""
    name = identifier.rsplit(".", 1)[-1].lower()
    for suffix in ("simulator", "os"):
        name = name.removesuffix(suffix)
    return name


def sdk_runtime_matches(installations: list[dict[str, Any]]) -> list[dict[str, str]]:
    """SDK-to-runtime resolutions from `simctl runtime match list` per installed Xcode.

    The `Chosen Runtime` line has two formats: `<name> (<version> - <build>) - <id>`
    when an installed runtime matched, or a bare build when only the SDK default
    applies. Both carry evidence, so return structured entries instead of raw builds.
    """
    matches: list[dict[str, str]] = []
    for item in installations:
        developer_dir = Path(item["path"]) / "Contents/Developer"
        if not developer_dir.is_dir():
            continue
        result = run(
            ["/usr/bin/xcrun", "simctl", "runtime", "match", "list"],
            timeout=60,
            env={**os.environ, "DEVELOPER_DIR": str(developer_dir)},
        )
        if result.returncode != 0:
            continue
        sdk_version = ""
        platform = ""
        for line in result.stdout.splitlines():
            if version_match := SDK_VERSION_LINE.search(line):
                sdk_version = version_match.group(1)
                continue
            if platform_match := PLATFORM_LINE.search(line):
                platform = platform_match.group(1)
                continue
            chosen_match = CHOSEN_RUNTIME_LINE.search(line)
            if not chosen_match:
                continue
            chosen = chosen_match.group(1).strip()
            build_match = CHOSEN_RUNTIME_BUILD.search(chosen)
            build = build_match.group(1) if build_match else chosen
            if build and build != "(null)":
                matches.append(
                    {
                        "platform": platform_fragment(platform),
                        "sdk_version": sdk_version,
                        "build": build,
                    }
                )
    return matches


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(piece) for piece in re.findall(r"\d+", version)) or (0,)


def inspect_runtimes(
    runtimes: list[dict[str, Any]],
    installations: list[dict[str, Any]],
) -> list[Candidate]:
    matches = sdk_runtime_matches(installations)
    installed_builds = {str(runtime.get("build", "")) for runtime in runtimes}
    resolved_builds = {
        entry["build"] for entry in matches if entry["build"] in installed_builds
    }
    # When an SDK's chosen build matches no installed runtime, `simctl` fell
    # back to the SDK default. The SDK still needs a runtime of that platform
    # and marketing version, so protect all matching runtimes conservatively.
    unresolved_platform_versions = {
        (entry["platform"], version_tuple(entry["sdk_version"])[:2])
        for entry in matches
        if entry["build"] not in installed_builds
    }

    candidates: list[Candidate] = []
    for runtime in runtimes:
        identifier = runtime.get("identifier")
        if not isinstance(identifier, str):
            continue
        platform = str(runtime.get("platformIdentifier", "unknown"))
        version = str(runtime.get("version", "0"))
        build = str(runtime.get("build", ""))
        size = runtime.get("sizeBytes")
        size = size if isinstance(size, int) else 0
        deletable = bool(runtime.get("deletable", False))
        chosen = build in resolved_builds
        version_protected = (
            platform_fragment(platform),
            version_tuple(version)[:2],
        ) in unresolved_platform_versions

        siblings = [
            other
            for other in runtimes
            if other is not runtime
            and other.get("platformIdentifier") == platform
        ]
        newer_version = next(
            (
                other
                for other in siblings
                if version_tuple(str(other.get("version", "0"))) > version_tuple(version)
            ),
            None,
        )
        chosen_sibling_same_version = next(
            (
                other
                for other in siblings
                if str(other.get("version", "")) == version
                and str(other.get("build", "")) in resolved_builds
                and str(other.get("build", "")) != build
            ),
            None,
        )

        evidence = [
            f"Runtime: {runtime.get('runtimeIdentifier', identifier)}",
            f"Version: {version} ({build})",
            f"Platform: {platform}",
            f"Last used: {runtime.get('lastUsedAt', 'unknown')}",
            f"Deletable via simctl: {deletable}",
        ]

        # A runtime is stale only with positive evidence: no installed SDK
        # chooses it or needs its platform version, and either a newer version
        # exists for the platform or a same-version sibling (the newer beta
        # case) is the one SDKs resolve to.
        stale = (
            deletable
            and matches
            and not chosen
            and not version_protected
            and (newer_version is not None or chosen_sibling_same_version is not None)
        )
        if stale:
            superseder = chosen_sibling_same_version or newer_version
            evidence.append(
                "Superseded by "
                f"{superseder.get('version', '?')} ({superseder.get('build', '?')}) "
                "on the same platform."
            )
            evidence.append("No installed Xcode SDK resolves to this runtime build.")
            candidates.append(
                Candidate(
                    id=f"runtime-stale:{identifier}",
                    category="Stale simulator runtime",
                    label=f"{platform.rsplit('.', 1)[-1]} {version} ({build})",
                    path=None,
                    bytes=size,
                    risk="destructive",
                    action="simctl-runtime-delete",
                    reason="No installed Xcode SDK uses this runtime and a newer one is installed.",
                    evidence=evidence,
                    regeneration_cost=(
                        "Devices on this runtime stop booting, and restoring it "
                        "requires a multi-gigabyte download."
                    ),
                    identifier=identifier,
                )
            )
            continue

        if chosen:
            evidence.append("Chosen by an installed Xcode SDK.")
        elif version_protected:
            evidence.append(
                "An installed SDK targets this platform version without an exact "
                "runtime build match; protected conservatively."
            )
        elif not matches:
            evidence.append("SDK match information unavailable; treating as in use.")
        candidates.append(
            Candidate(
                id=f"runtime:{identifier}",
                category="Simulator runtime",
                label=f"{platform.rsplit('.', 1)[-1]} {version} ({build})",
                path=None,
                bytes=size,
                risk="preserve",
                action="report-only",
                reason="An installed Xcode SDK may rely on this runtime.",
                evidence=evidence,
                regeneration_cost="The platform runtime must be downloaded again before compatible simulators can boot.",
                identifier=identifier,
            )
        )
    return candidates


def inspect_orphan_runtime_volumes(runtimes: list[dict[str, Any]]) -> list[Candidate]:
    volumes = Path("/Library/Developer/CoreSimulator/Volumes")
    if not volumes.is_dir():
        return []
    known_mounts = {
        str(runtime.get("mountPath", ""))
        for runtime in runtimes
    }
    candidates: list[Candidate] = []
    for entry in sorted(volumes.iterdir()):
        if not entry.is_dir():
            continue
        if str(entry) in known_mounts:
            continue
        candidates.append(
            Candidate(
                id=f"runtime-orphan-volume:{entry.name}",
                category="Orphan runtime volume",
                label=entry.name,
                path=str(entry),
                bytes=directory_size(entry),
                risk="preserve",
                action="report-only",
                reason="This mounted runtime volume is not reported by `simctl runtime list`.",
                evidence=[
                    "Run `xcrun simctl runtime scan-and-mount`, then delete the runtime by UUID.",
                    "Never unmount or remove runtime volumes manually.",
                ],
                regeneration_cost="Recovered runtimes may be deletable through simctl afterwards.",
            )
        )
    return candidates


def inspect_dyld_caches(home: Path, runtimes: list[dict[str, Any]]) -> list[Candidate]:
    root = home / "Library/Developer/CoreSimulator/Caches/dyld"
    if not root.is_dir():
        return []
    installed = {
        f"{runtime.get('runtimeIdentifier', '')}.{runtime.get('build', '')}"
        for runtime in runtimes
    }
    candidates: list[Candidate] = []
    for host_dir in sorted(root.iterdir()):
        if not host_dir.is_dir():
            continue
        for cache in sorted(host_dir.iterdir()):
            if not cache.is_dir() or not cache.name.startswith("com.apple.CoreSimulator.SimRuntime."):
                continue
            if cache.name in installed:
                continue
            candidates.append(
                candidate_for_path(
                    candidate_id=f"dyld-cache:{host_dir.name}:{cache.name}",
                    category="Simulator dyld cache",
                    label=cache.name,
                    path=cache,
                    risk="regenerable",
                    action="trash-path",
                    reason="No installed runtime matches this cached dyld image.",
                    evidence=[f"Installed runtimes do not include {cache.name}."],
                    regeneration_cost="Caches rebuild automatically when a matching runtime boots.",
                )
            )
    return candidates


DEVELOPER_ASSET_KEYWORDS = (
    "MetalToolchain",
    "DeveloperDocumentation",
    "DeveloperTools",
    "CoreDevice",
)


def inspect_managed_components() -> list[Candidate]:
    assets_root = Path("/System/Library/AssetsV2")
    if not assets_root.is_dir():
        return []
    candidates: list[Candidate] = []
    for entry in sorted(assets_root.glob("com_apple_MobileAsset_*")):
        if not entry.is_dir() or "SimulatorRuntime" in entry.name:
            continue
        if not any(keyword in entry.name for keyword in DEVELOPER_ASSET_KEYWORDS):
            continue
        component = entry.name.removeprefix("com_apple_MobileAsset_")
        candidates.append(
            Candidate(
                id=f"managed-component:{component}",
                category="Xcode-managed component",
                label=component,
                path=str(entry),
                bytes=directory_size(entry),
                risk="preserve",
                action="report-only",
                reason="Managed by Xcode and macOS asset services.",
                evidence=["Remove through Xcode Settings → Components, never from disk."],
                regeneration_cost="Xcode redownloads components on demand.",
            )
        )
    return candidates


def installed_xcodes(applications: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    paths = set(applications.glob("Xcode*.app"))
    spotlight = run(
        [
            "/usr/bin/mdfind",
            "kMDItemCFBundleIdentifier == 'com.apple.dt.Xcode'",
        ],
        timeout=60,
    )
    if spotlight.returncode == 0:
        paths.update(
            Path(line)
            for line in spotlight.stdout.splitlines()
            if line.endswith(".app") and Path(line).is_dir()
        )
    for path in sorted(paths):
        info = safe_plist(path / "Contents/Info.plist")
        if info.get("CFBundleIdentifier") != "com.apple.dt.Xcode":
            continue
        version = info.get("CFBundleShortVersionString")
        build = info.get("ProductBuildVersion") or info.get("DTXcodeBuild")
        mdls = run(
            [
                "/usr/bin/mdls",
                "-raw",
                "-name",
                "kMDItemLastUsedDate",
                str(path),
            ]
        )
        last_used = mdls.stdout.strip() if mdls.returncode == 0 else "(unknown)"
        results.append(
            {
                "path": path,
                "version": str(version) if version else None,
                "build": str(build) if build else None,
                "last_used": last_used,
                "bytes": directory_size(path),
            }
        )
    return results


def selected_xcode() -> str | None:
    result = run(["/usr/bin/xcode-select", "--print-path"])
    if result.returncode != 0:
        return None
    developer = Path(result.stdout.strip())
    return str(developer.parent.parent) if developer.name == "Developer" else str(developer)


def discover_installers(home: Path) -> set[Path]:
    found: set[Path] = set()
    query = (
        "(kMDItemFSName == 'Xcode*.xip'cd || "
        "kMDItemFSName == 'Xcode*.dmg'cd || "
        "kMDItemFSName == 'Xcode*.zip'cd)"
    )
    spotlight = run(["/usr/bin/mdfind", query], timeout=60)
    if spotlight.returncode == 0:
        for line in spotlight.stdout.splitlines():
            path = Path(line)
            if path.is_file():
                found.add(path)

    for root in (home / "Downloads", home / "Desktop"):
        if not root.is_dir():
            continue
        for pattern in ("Xcode*.xip", "Xcode*.dmg", "Xcode*.zip"):
            found.update(path for path in root.glob(pattern) if path.is_file())
    return found


def inspect_xcodes(
    home: Path,
    applications: Path,
    installations: list[dict[str, Any]] | None = None,
) -> list[Candidate]:
    if installations is None:
        installations = installed_xcodes(applications)
    selected = selected_xcode()
    candidates: list[Candidate] = []
    versions = {item["version"] for item in installations if item["version"]}

    for installer in sorted(discover_installers(home)):
        match = XCODE_INSTALLER_PATTERN.search(installer.name)
        parsed_version = match.group("version") if match else None
        evidence = [f"Installer filename: {installer.name}"]
        exact_match = parsed_version in versions if parsed_version else False
        if exact_match:
            evidence.append(f"Matching installed Xcode version: {parsed_version}")
        else:
            evidence.append("No exact installed version could be proven from the filename.")
        size = installer.stat().st_size
        candidates.append(
            Candidate(
                id=f"xcode-installer:{installer.name}:{installer.stat().st_ino}",
                category="Xcode installer",
                label=installer.name,
                path=str(installer.resolve()),
                bytes=size,
                risk="destructive" if exact_match else "preserve",
                action="trash-path" if exact_match else "report-only",
                reason=(
                    "An Xcode with the same parsed version is installed."
                    if exact_match
                    else "Installer identity could not be matched confidently."
                ),
                evidence=evidence,
                regeneration_cost="Offline reinstall is lost; the installer may be hard to download later.",
                fingerprint=fingerprint(installer, size),
            )
        )

    newest_stable = max(
        (
            item
            for item in installations
            if item["version"] and "beta" not in item["path"].name.lower()
        ),
        key=lambda item: tuple(int(piece) for piece in re.findall(r"\d+", item["version"])),
        default=None,
    )
    for item in installations:
        path = item["path"]
        evidence = [
            f"Version: {item['version'] or 'unknown'}",
            f"Build: {item['build'] or 'unknown'}",
            f"Spotlight last used: {item['last_used']}",
        ]
        if str(path.resolve()) == str(Path(selected).resolve()) if selected else False:
            evidence.append("Protected: selected by xcode-select.")
            action = "report-only"
            reason = "This is the active command-line Xcode."
        elif newest_stable and path != newest_stable["path"] and "beta" not in path.name.lower():
            evidence.append(f"Newer stable installation: {newest_stable['path'].name}")
            action = "report-only"
            reason = "Potentially superseded, but command-line or project-specific use cannot be ruled out."
        else:
            continue
        candidates.append(
            Candidate(
                id=f"xcode-app:{path.name}",
                category="Installed Xcode",
                label=path.name,
                path=str(path.resolve()),
                bytes=item["bytes"],
                risk="preserve",
                action=action,
                reason=reason,
                evidence=evidence,
                regeneration_cost="SDKs and toolchains may be unavailable until this exact Xcode is reinstalled.",
                fingerprint=fingerprint(path, item["bytes"]),
            )
        )
    return candidates


def archive_distribution_summary(info: dict[str, Any]) -> str | None:
    """Human-readable summary of the archive's Organizer distribution history."""
    distributions = info.get("Distributions")
    if not isinstance(distributions, list) or not distributions:
        return None
    summaries: list[str] = []
    for entry in distributions:
        if not isinstance(entry, dict):
            continue
        destination = entry.get("uploadDestination") or entry.get("destination", "unknown")
        event = entry.get("uploadEvent") or entry.get("exportEvent") or {}
        date = event.get("date", "") if isinstance(event, dict) else ""
        summaries.append(f"{destination} {date}".strip())
    return "; ".join(summaries) if summaries else "recorded"


def inspect_archives(home: Path) -> list[Candidate]:
    root = home / "Library/Developer/Xcode/Archives"
    if not root.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for archive in sorted(root.glob("*/*.xcarchive")):
        info = safe_plist(archive / "Info.plist")
        properties = info.get("ApplicationProperties", {})
        if not isinstance(properties, dict):
            properties = {}
        creation = info.get("CreationDate")
        entries.append(
            {
                "path": archive,
                "app_key": properties.get("CFBundleIdentifier")
                or str(info.get("Name", archive.stem)),
                "name": str(info.get("Name", archive.stem)),
                "version": str(properties.get("CFBundleShortVersionString", "?")),
                "build": str(properties.get("CFBundleVersion", "?")),
                "creation": creation if isinstance(creation, dt.datetime) else None,
                "distribution": archive_distribution_summary(info),
            }
        )

    newest_by_app: dict[str, dict[str, Any]] = {}
    for entry in entries:
        current = newest_by_app.get(entry["app_key"])
        entry_key = (
            version_tuple(entry["version"]),
            version_tuple(entry["build"]),
            entry["creation"] or dt.datetime.min,
        )
        if current is None or entry_key > (
            version_tuple(current["version"]),
            version_tuple(current["build"]),
            current["creation"] or dt.datetime.min,
        ):
            newest_by_app[entry["app_key"]] = entry

    candidates: list[Candidate] = []
    for entry in entries:
        archive = entry["path"]
        label = f"{entry['name']} {entry['version']} ({entry['build']})"
        newest = newest_by_app[entry["app_key"]]
        superseded = newest is not entry
        evidence = [f"Archive: {archive.name}"]
        if entry["creation"]:
            evidence.append(f"Archived: {entry['creation']:%Y-%m-%d}")

        if entry["distribution"]:
            evidence.append(f"Distributed: {entry['distribution']}")
            if superseded:
                evidence.append(
                    f"A newer archive exists ({newest['version']} ({newest['build']}))."
                )
            candidates.append(
                candidate_for_path(
                    candidate_id=f"archive:{archive.parent.name}:{archive.name}",
                    category="Archive",
                    label=label,
                    path=archive,
                    risk="preserve",
                    action="report-only",
                    reason="This build was uploaded or exported; its dSYMs may be needed for crash symbolication.",
                    evidence=evidence,
                    regeneration_cost="Irreplaceable for a distributed build unless an immutable backup exists.",
                )
            )
            continue

        evidence.append("Never uploaded or exported from this Mac (no Organizer distribution record).")
        if superseded:
            evidence.append(
                f"Superseded by {newest['version']} ({newest['build']})"
                + (
                    f" archived {newest['creation']:%Y-%m-%d}."
                    if newest["creation"]
                    else "."
                )
            )
            candidates.append(
                candidate_for_path(
                    candidate_id=f"archive-orphan:{archive.parent.name}:{archive.name}",
                    category="Orphan archive",
                    label=label,
                    path=archive,
                    risk="destructive",
                    action="trash-path",
                    reason="Never distributed and a newer archive of the same app exists; no shipped build depends on these dSYMs.",
                    evidence=evidence,
                    regeneration_cost="The exact binary cannot be rebuilt bit-identically, but nothing was shipped from it.",
                )
            )
            continue

        candidates.append(
            candidate_for_path(
                candidate_id=f"archive:{archive.parent.name}:{archive.name}",
                category="Archive",
                label=label,
                path=archive,
                risk="preserve",
                action="report-only",
                reason="Never distributed, but it is the newest archive for this app and may be pending distribution.",
                evidence=evidence,
                regeneration_cost="Irreplaceable if a distribution is still planned from this exact build.",
            )
        )
    return candidates


DEVICE_SUPPORT_PLATFORMS = ("iOS", "watchOS", "tvOS", "visionOS", "XROS")


def inspect_device_support(home: Path) -> list[Candidate]:
    candidates: list[Candidate] = []
    for platform in DEVICE_SUPPORT_PLATFORMS:
        root = home / f"Library/Developer/Xcode/{platform} DeviceSupport"
        if not root.is_dir():
            continue
        entries = [
            entry
            for entry in sorted(root.iterdir())
            if entry.is_dir() and not entry.is_symlink()
        ]
        if not entries:
            continue
        newest = max(entries, key=lambda entry: version_tuple(entry.name))
        for entry in entries:
            if entry is newest:
                candidates.append(
                    candidate_for_path(
                        candidate_id=f"device-support:{platform}:{entry.name}",
                        category="DeviceSupport",
                        label=f"{platform} {entry.name}",
                        path=entry,
                        risk="preserve",
                        action="report-only",
                        reason=f"Newest {platform} symbol set; needed for current devices.",
                        evidence=[f"Newest version folder under {platform} DeviceSupport."],
                        regeneration_cost="Requires reconnecting a compatible device.",
                    )
                )
                continue
            candidates.append(
                candidate_for_path(
                    candidate_id=f"device-support:{platform}:{entry.name}",
                    category="DeviceSupport",
                    label=f"{platform} {entry.name}",
                    path=entry,
                    risk="destructive",
                    action="trash-path",
                    reason=f"Older than the newest {platform} symbol set ({newest.name}).",
                    evidence=[
                        f"Newest sibling: {newest.name}",
                        "Symbols for retired OS builds may be unrecoverable once removed.",
                    ],
                    regeneration_cost=(
                        "Symbolicating crashes from this exact OS build needs a device "
                        "on that build; old symbols may be unavailable."
                    ),
                )
            )
    return candidates


def inspect_logs(home: Path) -> list[Candidate]:
    targets = [
        (home / "Library/Logs/CoreSimulator", "CoreSimulator logs", "coresimulator-logs"),
        (home / "Library/Developer/Xcode/iOS Device Logs", "iOS device logs", "ios-device-logs"),
    ]
    candidates: list[Candidate] = []
    for path, label, candidate_id in targets:
        if not path.is_dir():
            continue
        candidates.append(
            candidate_for_path(
                candidate_id=f"logs:{candidate_id}",
                category="Diagnostic logs",
                label=label,
                path=path,
                risk="regenerable",
                action="trash-path",
                reason="Diagnostic logs accumulate indefinitely and regrow on demand.",
                evidence=["Keep only if an active bug investigation depends on them."],
                regeneration_cost="Historical diagnostics are lost; new logs are written automatically.",
            )
        )
    return candidates


def inspect_xctest_devices(home: Path) -> list[Candidate]:
    root = home / "Library/Developer/XCTestDevices"
    if not root.is_dir():
        return []
    return [
        candidate_for_path(
            candidate_id="xctest-devices",
            category="XCTest devices",
            label="Cloned simulators from test runs",
            path=root,
            risk="regenerable",
            action="trash-path",
            reason="Test-run simulator clones are recreated on the next test run.",
            evidence=["Skip while a test run is in progress."],
            regeneration_cost="The next parallel test run re-clones its simulators.",
        )
    ]


def inspect_legacy_docsets(home: Path) -> list[Candidate]:
    root = home / "Library/Developer/Shared/Documentation/DocSets"
    if not root.is_dir():
        return []
    return [
        candidate_for_path(
            candidate_id=f"docset:{entry.name}",
            category="Legacy DocSet",
            label=entry.name,
            path=entry,
            risk="destructive",
            action="trash-path",
            reason="Legacy downloadable DocSets are no longer distributed by current Xcodes.",
            evidence=["Confirm no offline documentation workflow depends on this DocSet."],
            regeneration_cost="Old DocSets may be impossible to download again.",
        )
        for entry in sorted(root.iterdir())
        if entry.is_dir() and not entry.is_symlink()
    ]


def inspect_cocoapods(home: Path) -> list[Candidate]:
    root = home / "Library/Caches/CocoaPods"
    if not root.is_dir():
        return []
    return [
        candidate_for_path(
            candidate_id="cocoapods:cache",
            category="CocoaPods cache",
            label="CocoaPods download cache",
            path=root,
            risk="regenerable",
            action="trash-path",
            reason="Pod downloads are re-fetched by `pod install` when needed.",
            evidence=["Project Pods/ directories are untouched."],
            regeneration_cost="Pods must be downloaded again on the next install.",
        )
    ]


def inspect_project_builds(scan_roots: list[Path], home: Path) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen: set[Path] = set()
    for root in scan_roots:
        root = root.expanduser().resolve(strict=False)
        if not root.is_dir():
            continue
        for current, directories, _ in os.walk(root, followlinks=False):
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            directories[:] = [
                name
                for name in directories
                if name not in SKIP_SCAN_NAMES and not (current_path / name).is_symlink()
            ]
            if depth > 5:
                directories[:] = []
                continue
            for name in list(directories):
                if name not in {".build", ".derived-data"}:
                    continue
                path = (current_path / name).resolve(strict=False)
                if path in seen:
                    continue
                seen.add(path)
                try:
                    relative = path.relative_to(home)
                except ValueError:
                    relative = path
                candidates.append(
                    candidate_for_path(
                        candidate_id=f"project-build:{str(relative).replace('/', ':')}",
                        category="Project-local build data",
                        label=str(relative),
                        path=path,
                        risk="regenerable",
                        action="trash-path",
                        reason="Generated project-local build or DerivedData directory.",
                        evidence=[f"Discovered under scan root: {root}"],
                        regeneration_cost="The project or package must rebuild and resolve dependencies.",
                    )
                )
                directories.remove(name)
    return candidates


def audit(args: argparse.Namespace) -> dict[str, Any]:
    home = Path(args.home).expanduser().resolve()
    applications = Path(args.applications).expanduser().resolve()
    scan_roots = [Path(value) for value in args.scan_root]
    installations = installed_xcodes(applications)
    runtimes = runtime_inventory()
    candidates = [
        *inspect_derived_data(home),
        *inspect_documentation(home),
        *inspect_simulators(home),
        *inspect_runtimes(runtimes, installations),
        *inspect_orphan_runtime_volumes(runtimes),
        *inspect_dyld_caches(home, runtimes),
        *inspect_managed_components(),
        *inspect_xcodes(home, applications, installations),
        *inspect_archives(home),
        *inspect_device_support(home),
        *inspect_logs(home),
        *inspect_xctest_devices(home),
        *inspect_legacy_docsets(home),
        *inspect_cocoapods(home),
        *inspect_project_builds(scan_roots, home),
    ]
    candidates.sort(key=lambda item: (-item.bytes, item.category, item.id))
    bytes_by_risk = {
        risk: sum(item.bytes for item in candidates if item.risk == risk)
        for risk in ("regenerable", "destructive", "preserve")
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host": os.uname().nodename,
        "home": str(home),
        "applications": str(applications),
        "candidate_bytes": sum(item.bytes for item in candidates),
        "actionable_bytes": sum(
            item.bytes for item in candidates if item.action != "report-only"
        ),
        "bytes_by_risk": bytes_by_risk,
        "candidates": [item.as_dict() for item in candidates],
    }


def markdown_report(payload: dict[str, Any]) -> str:
    candidates = payload["candidates"]
    lines = [
        "# Xcode disk cleanup audit",
        "",
        f"Created: {payload['created_at']}",
        "",
        "No cleanup has been performed. Candidate sizes are not additive on APFS.",
        "",
        "| ID | Category | Risk | Recoverable | Candidate | Action |",
        "|---|---|---:|---:|---|---|",
    ]
    for item in candidates:
        lines.append(
            f"| `{item['id']}` | {item['category']} | {item['risk']} | "
            f"{gibibytes(item['bytes'])} | {item['label']} | {item['action']} |"
        )
    lines.extend(
        [
            "",
        f"Actionable candidate size: **{gibibytes(payload.get('actionable_bytes', 0))}**",
        f"Preserve/review size: **{gibibytes(payload.get('bytes_by_risk', {}).get('preserve', 0))}**",
            "",
            "Review each item’s evidence and regeneration cost before approving exact IDs.",
        ]
    )
    return "\n".join(lines) + "\n"


def process_uses_path(path: Path) -> bool:
    result = run(["/bin/ps", "-axo", "command="])
    if result.returncode != 0:
        return True
    needle = str(path)
    return any(needle in line for line in result.stdout.splitlines())


def validate_fingerprint(path: Path, expected: dict[str, int] | None) -> None:
    if expected is None:
        raise RuntimeError(f"No audit fingerprint for {path}")
    if path.is_symlink():
        raise RuntimeError(f"Refusing symlink candidate: {path}")
    current = fingerprint(path)
    if current is None:
        raise RuntimeError(f"Candidate no longer exists: {path}")
    for key in ("device", "inode"):
        if current[key] != expected[key]:
            raise RuntimeError(f"Candidate identity changed since audit: {path}")


def trash(path: Path, trash_root: Path) -> Path:
    trash_root.mkdir(parents=True, exist_ok=True)
    destination = trash_root / path.name
    if destination.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = trash_root / f"{path.name}-{stamp}"
    return Path(shutil.move(str(path), str(destination)))


def apply_cleanup(args: argparse.Namespace) -> dict[str, Any]:
    if args.confirm != CONFIRMATION_PHRASE:
        raise RuntimeError(
            "Explicit confirmation is required. The agent must first show the itemized audit."
        )
    payload = json.loads(Path(args.audit_file).read_text())
    by_id = {item["id"]: item for item in payload.get("candidates", [])}
    requested = list(dict.fromkeys(args.ids))
    if not requested:
        raise RuntimeError("At least one exact candidate ID is required.")
    unknown = [item_id for item_id in requested if item_id not in by_id]
    if unknown:
        raise RuntimeError(f"Unknown candidate IDs: {', '.join(unknown)}")

    selected = [by_id[item_id] for item_id in requested]
    blocked = [item["id"] for item in selected if item["action"] == "report-only"]
    if blocked:
        raise RuntimeError(f"Report-only candidates cannot be applied: {', '.join(blocked)}")

    irreversible = [item for item in selected if item["action"].startswith("simctl-")]
    if irreversible and args.confirm_irreversible != IRREVERSIBLE_PHRASE:
        raise RuntimeError("Simulator deletion requires separate irreversible confirmation.")

    trash_root = Path(args.trash_dir).expanduser().resolve()
    results: list[dict[str, Any]] = []
    before = shutil.disk_usage(Path.home()).free
    for item in selected:
        action = item["action"]
        path = Path(item["path"]) if item.get("path") else None
        if path:
            validate_fingerprint(path, item.get("fingerprint"))
            if process_uses_path(path):
                raise RuntimeError(f"A running process references {path}")

        if action == "trash-path":
            assert path is not None
            destination = trash(path, trash_root)
            results.append({"id": item["id"], "result": "moved-to-trash", "path": str(destination)})
        elif action == "simctl-delete-device":
            identifier = item.get("identifier")
            if not identifier:
                raise RuntimeError(f"Missing simulator identifier for {item['id']}")
            command = ["/usr/bin/xcrun", "simctl"]
            if item.get("device_set"):
                command.extend(["--set", item["device_set"]])
            command.extend(["delete", identifier])
            result = run(command, timeout=60)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or f"simctl failed for {identifier}")
            results.append({"id": item["id"], "result": "deleted-by-simctl"})
        elif action == "simctl-runtime-delete":
            identifier = item.get("identifier")
            if not identifier:
                raise RuntimeError(f"Missing runtime identifier for {item['id']}")
            result = run(
                ["/usr/bin/xcrun", "simctl", "runtime", "delete", identifier],
                timeout=300,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    result.stderr.strip() or f"simctl runtime delete failed for {identifier}"
                )
            results.append({"id": item["id"], "result": "runtime-deleted-by-simctl"})
        else:
            raise RuntimeError(f"Unsupported action: {action}")

    after = shutil.disk_usage(Path.home()).free
    return {
        "applied_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "results": results,
        "free_bytes_before": before,
        "free_bytes_after": after,
        "immediate_reclaimed_bytes": max(0, after - before),
        "note": "Items moved to Trash do not reclaim blocks until Trash is emptied.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="Run a read-only storage audit.")
    audit_parser.add_argument("--home", default=str(Path.home()))
    audit_parser.add_argument("--applications", default="/Applications")
    audit_parser.add_argument("--scan-root", action="append", default=[])
    audit_parser.add_argument("--output-dir", default=".xcode-disk-cleanup-audit")

    apply_parser = subparsers.add_parser("apply", help="Apply exact approved candidate IDs.")
    apply_parser.add_argument("--audit-file", required=True)
    apply_parser.add_argument("--ids", nargs="+", required=True)
    apply_parser.add_argument("--confirm", required=True)
    apply_parser.add_argument("--confirm-irreversible")
    apply_parser.add_argument("--trash-dir", default=str(Path.home() / ".Trash"))
    apply_parser.add_argument("--output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "audit":
            payload = audit(args)
            output_dir = Path(args.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            json_path = output_dir / "audit.json"
            markdown_path = output_dir / "report.md"
            json_path.write_text(json.dumps(payload, indent=2) + "\n")
            markdown_path.write_text(markdown_report(payload))
            print(markdown_path)
            return 0

        result = apply_cleanup(args)
        rendered = json.dumps(result, indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(rendered)
        print(rendered, end="")
        return 0
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
