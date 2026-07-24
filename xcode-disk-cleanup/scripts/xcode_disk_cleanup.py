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
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    result = run(["/usr/bin/du", "-sk", str(path)], timeout=180)
    if result.returncode != 0 or not result.stdout.strip():
        return 0
    return int(result.stdout.split()[0]) * 1024


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
        if last_accessed:
            evidence.append(f"Last accessed: {last_accessed}")

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
                candidates.append(
                    candidate_for_path(
                        candidate_id=f"derived-packages-only:{entry.name}",
                        category="DerivedData",
                        label=f"Metadata-free package cache: {entry.name}",
                        path=entry,
                        risk="regenerable",
                        action="trash-path",
                        reason="No Xcode workspace metadata or build products were found.",
                        evidence=[f"Contents: {', '.join(sorted(children))}"],
                        regeneration_cost="Dependencies must be resolved or downloaded again.",
                    )
                )

    shared_cache_names = {
        "ModuleCache.noindex": "Compiler modules rebuild on future builds.",
        "SDKExplicitPrecompiledModules": "SDK modules rebuild on future builds.",
    }
    for name, cost in shared_cache_names.items():
        path = root / name
        if path.is_dir():
            candidates.append(
                candidate_for_path(
                    candidate_id=f"shared-cache:{name}",
                    category="Shared build cache",
                    label=name,
                    path=path,
                    risk="regenerable",
                    action="trash-path",
                    reason="Shared compiler cache; safe to regenerate but expensive for active work.",
                    evidence=["Shared by multiple Xcode projects."],
                    regeneration_cost=cost,
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
            reason=f"A newer cache ({latest.name}) exists.",
            evidence=[f"Newest sibling cache: {latest.name}"],
            regeneration_cost="Offline documentation may need to be downloaded again.",
        )
        for entry in versions[:-1]
    ]


def inspect_package_caches(home: Path) -> list[Candidate]:
    swiftpm = home / "Library/Caches/org.swift.swiftpm"
    if not swiftpm.is_dir():
        return []
    return [
        candidate_for_path(
            candidate_id="swiftpm:global-cache",
            category="SwiftPM cache",
            label="Global SwiftPM download cache",
            path=swiftpm,
            risk="regenerable",
            action="swiftpm-purge-cache",
            reason="SwiftPM can recreate repository and artifact downloads.",
            evidence=["Prefer `swift package purge-cache` when supported by the selected toolchain."],
            regeneration_cost="Dependencies must be downloaded again; concurrent builds may fail.",
        )
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


def inspect_runtimes() -> list[Candidate]:
    payload = simctl_json(["list", "--json", "runtimes"])
    runtimes = payload.get("runtimes", [])
    if not isinstance(runtimes, list):
        return []
    candidates: list[Candidate] = []
    for runtime in runtimes:
        if not isinstance(runtime, dict):
            continue
        identifier = runtime.get("identifier")
        if not isinstance(identifier, str):
            continue
        raw_path = runtime.get("bundlePath") or runtime.get("path")
        path = Path(raw_path) if isinstance(raw_path, str) else None
        size = directory_size(path) if path and path.exists() else 0
        evidence = [
            f"Identifier: {identifier}",
            f"Version: {runtime.get('version', 'unknown')}",
            f"Availability: {runtime.get('availability', 'unknown')}",
        ]
        if path:
            evidence.append(f"Bundle path: {path}")
        candidates.append(
            Candidate(
                id=f"runtime:{identifier}",
                category="Simulator runtime",
                label=str(runtime.get("name", identifier)),
                path=str(path.resolve(strict=False)) if path else None,
                bytes=size,
                risk="preserve",
                action="report-only",
                reason="Runtime use must be checked against installed Xcodes and current projects.",
                evidence=evidence,
                regeneration_cost="The platform runtime must be downloaded again before compatible simulators can boot.",
                identifier=identifier,
                fingerprint=fingerprint(path, size) if path and path.exists() else None,
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


def inspect_xcodes(home: Path, applications: Path) -> list[Candidate]:
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


def inspect_archives(home: Path) -> list[Candidate]:
    root = home / "Library/Developer/Xcode/Archives"
    if not root.is_dir():
        return []
    candidates: list[Candidate] = []
    for archive in sorted(root.glob("*/*.xcarchive")):
        info = safe_plist(archive / "Info.plist")
        app = info.get("Name", archive.stem)
        properties = info.get("ApplicationProperties", {})
        if not isinstance(properties, dict):
            properties = {}
        version = properties.get("CFBundleShortVersionString", "?")
        build = properties.get("CFBundleVersion", "?")
        candidates.append(
            candidate_for_path(
                candidate_id=f"archive:{archive.parent.name}:{archive.name}",
                category="Archive",
                label=f"{app} {version} ({build})",
                path=archive,
                risk="preserve",
                action="report-only",
                reason="Archives contain exact binaries and dSYMs required for crash symbolication.",
                evidence=[f"Archive: {archive.name}"],
                regeneration_cost="Irreplaceable for a distributed build unless an immutable backup exists.",
            )
        )
    return candidates


def inspect_device_support(home: Path) -> list[Candidate]:
    root = home / "Library/Developer/Xcode/iOS DeviceSupport"
    if not root.is_dir():
        return []
    return [
        candidate_for_path(
            candidate_id=f"device-support:{entry.name}",
            category="DeviceSupport",
            label=entry.name,
            path=entry,
            risk="preserve",
            action="report-only",
            reason="Device symbols can be needed for historical crash symbolication.",
            evidence=["Review against devices and OS versions still supported."],
            regeneration_cost="Requires reconnecting a compatible device; old symbols may be unavailable.",
        )
        for entry in sorted(root.iterdir())
        if entry.is_dir() and not entry.is_symlink()
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
    candidates = [
        *inspect_derived_data(home),
        *inspect_documentation(home),
        *inspect_package_caches(home),
        *inspect_simulators(home),
        *inspect_runtimes(),
        *inspect_xcodes(home, applications),
        *inspect_archives(home),
        *inspect_device_support(home),
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
        elif action == "swiftpm-purge-cache":
            help_result = run(["/usr/bin/swift", "package", "--help"])
            if "purge-cache" not in help_result.stdout:
                raise RuntimeError("Selected Swift toolchain does not support `swift package purge-cache`.")
            result = run(["/usr/bin/swift", "package", "purge-cache"], timeout=600)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "SwiftPM cache purge failed.")
            results.append({"id": item["id"], "result": "purged-by-swiftpm"})
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
