from __future__ import annotations

import argparse
import importlib.util
import json
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = (
    Path(__file__).parents[1]
    / "xcode-disk-cleanup"
    / "scripts"
    / "xcode_disk_cleanup.py"
)
SPEC = importlib.util.spec_from_file_location("xcode_disk_cleanup", SCRIPT)
assert SPEC and SPEC.loader
cleanup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cleanup
SPEC.loader.exec_module(cleanup)


class AuditTests(unittest.TestCase):
    def test_missing_workspace_derived_data_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            derived = (
                home
                / "Library/Developer/Xcode/DerivedData"
                / "DeletedProject-abcd"
            )
            derived.mkdir(parents=True)
            with (derived / "info.plist").open("wb") as stream:
                plistlib.dump(
                    {"WorkspacePath": str(home / "Developer/DeletedProject.xcodeproj")},
                    stream,
                )
            (derived / "Build").mkdir()
            (derived / "Build/artifact").write_bytes(b"x" * 1024)

            candidates = cleanup.inspect_derived_data(home)

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].action, "trash-path")
            self.assertEqual(candidates[0].risk, "regenerable")
            self.assertIn("missing workspace", candidates[0].label)

    def test_existing_workspace_derived_data_is_not_suggested(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            workspace = home / "Developer/Current.xcodeproj"
            workspace.mkdir(parents=True)
            derived = home / "Library/Developer/Xcode/DerivedData/Current-abcd"
            derived.mkdir(parents=True)
            with (derived / "info.plist").open("wb") as stream:
                plistlib.dump({"WorkspacePath": str(workspace)}, stream)
            (derived / "Build").mkdir()

            candidates = cleanup.inspect_derived_data(home)

            self.assertEqual(candidates, [])

    def test_documentation_keeps_newest_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root = home / "Library/Developer/Xcode/DocumentationCache"
            for version in ("v100", "v200", "v300"):
                path = root / version
                path.mkdir(parents=True)
                (path / "index").write_bytes(version.encode())

            candidates = cleanup.inspect_documentation(home)

            self.assertEqual([item.label for item in candidates], [
                "Older documentation cache v100",
                "Older documentation cache v200",
            ])

    @mock.patch.object(cleanup, "discover_installers")
    @mock.patch.object(cleanup, "selected_xcode", return_value=None)
    @mock.patch.object(cleanup, "installed_xcodes")
    def test_matching_installer_is_actionable(
        self,
        installed_xcodes: mock.Mock,
        _: mock.Mock,
        discover_installers: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer = root / "Xcode_26.4.xip"
            installer.write_bytes(b"x" * 512)
            app = root / "Applications/Xcode.app"
            app.mkdir(parents=True)
            installed_xcodes.return_value = [
                {
                    "path": app,
                    "version": "26.4",
                    "build": "17A1",
                    "last_used": "2026-07-01",
                    "bytes": 100,
                }
            ]
            discover_installers.return_value = {installer}

            candidates = cleanup.inspect_xcodes(root, root / "Applications")

            installer_candidate = next(
                item for item in candidates if item.category == "Xcode installer"
            )
            self.assertEqual(installer_candidate.action, "trash-path")
            self.assertEqual(installer_candidate.risk, "destructive")

    def test_archives_remain_report_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            archive = (
                home
                / "Library/Developer/Xcode/Archives/2026-07-24/App.xcarchive"
            )
            archive.mkdir(parents=True)
            with (archive / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "Name": "App",
                        "ApplicationProperties": {
                            "CFBundleShortVersionString": "1.0",
                            "CFBundleVersion": "42",
                        },
                    },
                    stream,
                )

            candidates = cleanup.inspect_archives(home)

            self.assertEqual(candidates[0].action, "report-only")
            self.assertEqual(candidates[0].risk, "preserve")

    RUNTIME_BETA_OLD = {
        "identifier": "AAAA-1111",
        "runtimeIdentifier": "com.apple.CoreSimulator.SimRuntime.iOS-27-0",
        "platformIdentifier": "com.apple.platform.iphonesimulator",
        "version": "27.0",
        "build": "25A5306g",
        "deletable": True,
        "sizeBytes": 9 * 1024**3,
        "lastUsedAt": "2026-06-01T10:00:00Z",
    }
    RUNTIME_BETA_NEW = {
        "identifier": "BBBB-2222",
        "runtimeIdentifier": "com.apple.CoreSimulator.SimRuntime.iOS-27-0",
        "platformIdentifier": "com.apple.platform.iphonesimulator",
        "version": "27.0",
        "build": "25A5316j",
        "deletable": True,
        "sizeBytes": 9 * 1024**3,
        "lastUsedAt": "2026-07-20T10:00:00Z",
    }
    RUNTIME_WATCH_OLD = {
        "identifier": "CCCC-3333",
        "runtimeIdentifier": "com.apple.CoreSimulator.SimRuntime.watchOS-26-4",
        "platformIdentifier": "com.apple.platform.watchsimulator",
        "version": "26.4",
        "build": "23T500",
        "deletable": True,
        "sizeBytes": 4 * 1024**3,
        "lastUsedAt": "2026-03-01T10:00:00Z",
    }
    RUNTIME_WATCH_NEW = {
        "identifier": "DDDD-4444",
        "runtimeIdentifier": "com.apple.CoreSimulator.SimRuntime.watchOS-26-5",
        "platformIdentifier": "com.apple.platform.watchsimulator",
        "version": "26.5",
        "build": "23T600",
        "deletable": True,
        "sizeBytes": 4 * 1024**3,
        "lastUsedAt": "2026-07-20T10:00:00Z",
    }

    @mock.patch.object(
        cleanup,
        "sdk_chosen_runtime_builds",
        return_value={"25A5316j", "23T600"},
    )
    def test_superseded_beta_runtime_is_deletable(self, _: mock.Mock) -> None:
        runtimes = [dict(self.RUNTIME_BETA_OLD), dict(self.RUNTIME_BETA_NEW)]

        candidates = cleanup.inspect_runtimes(runtimes, [])

        stale = next(item for item in candidates if item.identifier == "AAAA-1111")
        kept = next(item for item in candidates if item.identifier == "BBBB-2222")
        self.assertEqual(stale.action, "simctl-runtime-delete")
        self.assertEqual(stale.risk, "destructive")
        self.assertEqual(kept.action, "report-only")
        self.assertEqual(kept.risk, "preserve")

    @mock.patch.object(
        cleanup,
        "sdk_chosen_runtime_builds",
        return_value={"25A5316j", "23T600"},
    )
    def test_older_version_runtime_is_deletable(self, _: mock.Mock) -> None:
        runtimes = [dict(self.RUNTIME_WATCH_OLD), dict(self.RUNTIME_WATCH_NEW)]

        candidates = cleanup.inspect_runtimes(runtimes, [])

        stale = next(item for item in candidates if item.identifier == "CCCC-3333")
        self.assertEqual(stale.action, "simctl-runtime-delete")
        self.assertIn(
            "Superseded by 26.5 (23T600) on the same platform.",
            stale.evidence,
        )

    @mock.patch.object(cleanup, "sdk_chosen_runtime_builds", return_value=set())
    def test_runtimes_stay_report_only_without_sdk_evidence(self, _: mock.Mock) -> None:
        runtimes = [dict(self.RUNTIME_WATCH_OLD), dict(self.RUNTIME_WATCH_NEW)]

        candidates = cleanup.inspect_runtimes(runtimes, [])

        self.assertTrue(all(item.action == "report-only" for item in candidates))
        self.assertTrue(all(item.risk == "preserve" for item in candidates))

    @mock.patch.object(
        cleanup,
        "sdk_chosen_runtime_builds",
        return_value={"25A5316j"},
    )
    def test_non_deletable_runtime_is_never_actionable(self, _: mock.Mock) -> None:
        pinned = dict(self.RUNTIME_BETA_OLD, deletable=False)
        runtimes = [pinned, dict(self.RUNTIME_BETA_NEW)]

        candidates = cleanup.inspect_runtimes(runtimes, [])

        stale = next(item for item in candidates if item.identifier == "AAAA-1111")
        self.assertEqual(stale.action, "report-only")

    def test_device_support_keeps_newest_per_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            for platform, versions in {
                "iOS": ("iPhone15,2 18.5 (22F76)", "iPhone15,2 26.0 (23A100)"),
                "watchOS": ("Watch7,1 26.4 (23T500)",),
            }.items():
                for version in versions:
                    folder = (
                        home
                        / f"Library/Developer/Xcode/{platform} DeviceSupport"
                        / version
                    )
                    folder.mkdir(parents=True)
                    (folder / "Symbols").mkdir()

            candidates = cleanup.inspect_device_support(home)

            by_label = {item.label: item for item in candidates}
            self.assertEqual(
                by_label["iOS iPhone15,2 18.5 (22F76)"].action, "trash-path"
            )
            self.assertEqual(
                by_label["iOS iPhone15,2 18.5 (22F76)"].risk, "destructive"
            )
            self.assertEqual(
                by_label["iOS iPhone15,2 26.0 (23A100)"].action, "report-only"
            )
            self.assertEqual(
                by_label["watchOS Watch7,1 26.4 (23T500)"].action, "report-only"
            )

    def test_dyld_cache_for_missing_runtime_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root = home / "Library/Developer/CoreSimulator/Caches/dyld/25A100"
            stale = root / "com.apple.CoreSimulator.SimRuntime.iOS-26-0.23A200"
            current = root / "com.apple.CoreSimulator.SimRuntime.iOS-27-0.25A5316j"
            stale.mkdir(parents=True)
            current.mkdir(parents=True)
            runtimes = [
                {
                    "runtimeIdentifier": "com.apple.CoreSimulator.SimRuntime.iOS-27-0",
                    "build": "25A5316j",
                }
            ]

            candidates = cleanup.inspect_dyld_caches(home, runtimes)

            self.assertEqual(len(candidates), 1)
            self.assertIn("iOS-26-0.23A200", candidates[0].label)
            self.assertEqual(candidates[0].action, "trash-path")

    @mock.patch.object(cleanup, "directory_size", return_value=42)
    @mock.patch.object(
        cleanup,
        "run",
        return_value=mock.Mock(returncode=0, stdout="", stderr=""),
    )
    def test_xcode_version_manager_is_not_treated_as_xcode(
        self,
        _: mock.Mock,
        __: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            applications = Path(temporary)
            xcode = applications / "Xcode-26.6.app/Contents"
            manager = applications / "Xcodes.app/Contents"
            xcode.mkdir(parents=True)
            manager.mkdir(parents=True)
            with (xcode / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.apple.dt.Xcode",
                        "CFBundleShortVersionString": "26.6",
                    },
                    stream,
                )
            with (manager / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.xcodesorg.xcodes",
                        "CFBundleShortVersionString": "3.0",
                    },
                    stream,
                )

            installations = cleanup.installed_xcodes(applications)

            self.assertEqual(len(installations), 1)
            self.assertEqual(installations[0]["path"].name, "Xcode-26.6.app")


class ApplyTests(unittest.TestCase):
    def write_audit(self, root: Path, candidates: list[dict]) -> Path:
        path = root / "audit.json"
        path.write_text(json.dumps({"schema_version": 1, "candidates": candidates}))
        return path

    def args(self, audit: Path, trash: Path, **overrides: object) -> argparse.Namespace:
        values = {
            "audit_file": str(audit),
            "ids": ["candidate"],
            "confirm": cleanup.CONFIRMATION_PHRASE,
            "confirm_irreversible": None,
            "trash_dir": str(trash),
            "output": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_apply_requires_exact_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit = self.write_audit(root, [])
            with self.assertRaisesRegex(RuntimeError, "Explicit confirmation"):
                cleanup.apply_cleanup(
                    self.args(audit, root / "Trash", confirm="yes")
                )

    @mock.patch.object(cleanup, "process_uses_path", return_value=False)
    def test_approved_path_moves_to_trash(self, _: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "DerivedData"
            candidate.mkdir()
            (candidate / "artifact").write_bytes(b"x")
            size = cleanup.directory_size(candidate)
            audit = self.write_audit(
                root,
                [
                    {
                        "id": "candidate",
                        "action": "trash-path",
                        "path": str(candidate),
                        "fingerprint": cleanup.fingerprint(candidate, size),
                    }
                ],
            )

            result = cleanup.apply_cleanup(self.args(audit, root / "Trash"))

            self.assertFalse(candidate.exists())
            self.assertTrue((root / "Trash/DerivedData").exists())
            self.assertEqual(result["results"][0]["result"], "moved-to-trash")

    def test_report_only_candidate_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit = self.write_audit(
                root,
                [{"id": "candidate", "action": "report-only"}],
            )

            with self.assertRaisesRegex(RuntimeError, "Report-only"):
                cleanup.apply_cleanup(self.args(audit, root / "Trash"))

    @mock.patch.object(cleanup, "process_uses_path", return_value=False)
    def test_simulator_requires_separate_confirmation(self, _: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            device = root / "Devices/ABC"
            device.mkdir(parents=True)
            audit = self.write_audit(
                root,
                [
                    {
                        "id": "candidate",
                        "action": "simctl-delete-device",
                        "path": str(device),
                        "identifier": "ABC",
                        "fingerprint": cleanup.fingerprint(device),
                    }
                ],
            )

            with self.assertRaisesRegex(RuntimeError, "separate irreversible"):
                cleanup.apply_cleanup(self.args(audit, root / "Trash"))

    @mock.patch.object(
        cleanup,
        "run",
        return_value=mock.Mock(returncode=0, stdout="", stderr=""),
    )
    @mock.patch.object(cleanup, "process_uses_path", return_value=False)
    def test_preview_simulator_uses_preview_device_set(
        self,
        _: mock.Mock,
        run: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            device = root / "Previews/Devices/ABC"
            device.mkdir(parents=True)
            audit = self.write_audit(
                root,
                [
                    {
                        "id": "candidate",
                        "action": "simctl-delete-device",
                        "path": str(device),
                        "identifier": "ABC",
                        "device_set": "previews",
                        "fingerprint": cleanup.fingerprint(device),
                    }
                ],
            )

            cleanup.apply_cleanup(
                self.args(
                    audit,
                    root / "Trash",
                    confirm_irreversible=cleanup.IRREVERSIBLE_PHRASE,
                )
            )

            run.assert_any_call(
                ["/usr/bin/xcrun", "simctl", "--set", "previews", "delete", "ABC"],
                timeout=60,
            )

    def test_runtime_delete_requires_irreversible_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit = self.write_audit(
                root,
                [
                    {
                        "id": "candidate",
                        "action": "simctl-runtime-delete",
                        "path": None,
                        "identifier": "AAAA-1111",
                    }
                ],
            )

            with self.assertRaisesRegex(RuntimeError, "separate irreversible"):
                cleanup.apply_cleanup(self.args(audit, root / "Trash"))

    @mock.patch.object(
        cleanup,
        "run",
        return_value=mock.Mock(returncode=0, stdout="", stderr=""),
    )
    def test_runtime_delete_invokes_simctl_runtime_delete(
        self,
        run: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit = self.write_audit(
                root,
                [
                    {
                        "id": "candidate",
                        "action": "simctl-runtime-delete",
                        "path": None,
                        "identifier": "AAAA-1111",
                    }
                ],
            )

            result = cleanup.apply_cleanup(
                self.args(
                    audit,
                    root / "Trash",
                    confirm_irreversible=cleanup.IRREVERSIBLE_PHRASE,
                )
            )

            run.assert_any_call(
                ["/usr/bin/xcrun", "simctl", "runtime", "delete", "AAAA-1111"],
                timeout=300,
            )
            self.assertEqual(
                result["results"][0]["result"], "runtime-deleted-by-simctl"
            )

    @mock.patch.object(cleanup, "process_uses_path", return_value=False)
    def test_changed_candidate_identity_is_blocked(self, _: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "Cache"
            candidate.mkdir()
            stale = cleanup.fingerprint(candidate)
            candidate.rmdir()
            candidate.mkdir()
            audit = self.write_audit(
                root,
                [
                    {
                        "id": "candidate",
                        "action": "trash-path",
                        "path": str(candidate),
                        "fingerprint": stale,
                    }
                ],
            )

            with self.assertRaisesRegex(RuntimeError, "identity changed"):
                cleanup.apply_cleanup(self.args(audit, root / "Trash"))


class ReportTests(unittest.TestCase):
    def test_markdown_reports_gibibytes_and_no_mutation(self) -> None:
        payload = {
            "created_at": "2026-07-24T00:00:00+00:00",
            "candidate_bytes": 2 * 1024**3,
            "candidates": [
                {
                    "id": "cache",
                    "category": "Cache",
                    "risk": "regenerable",
                    "bytes": 2 * 1024**3,
                    "label": "Cache",
                    "action": "trash-path",
                }
            ],
        }

        report = cleanup.markdown_report(payload)

        self.assertIn("2.00 GiB", report)
        self.assertIn("No cleanup has been performed", report)


if __name__ == "__main__":
    unittest.main()
