# Simulator devices and runtimes

Devices and runtimes are different assets:

- A device stores installed apps, app data, keychains, settings, and test state.
- A runtime is the shared platform OS image used by multiple devices.

## Read-only inventory

```bash
xcrun simctl list --json devices
xcrun simctl list --json runtimes
xcrun simctl help
xcrun simctl help runtime
```

Measure each device folder only after joining its UDID to `simctl` output.

## Unavailable devices

A device whose runtime is unavailable cannot boot. It is a strong cleanup
candidate, but deletion permanently loses its local state. Use:

```bash
xcrun simctl delete <UDID>
```

The broad `xcrun simctl delete unavailable` command is acceptable only when every
unavailable device has been itemized and approved. Never use `delete all` as a
routine cleanup.

## Installed runtimes

Runtimes are multi-gigabyte platform images managed by CoreSimulator and the
MobileAsset system. Inventory them with:

```bash
xcrun simctl runtime list -j
```

Each entry reports `deletable`, `lastUsedAt`, `sizeBytes`, and the runtime build.

### Staleness evidence

Installing a newer Xcode beta frequently leaves older beta platform runtimes
behind; they keep appearing in Xcode Settings → Components long after any SDK
uses them. A runtime is a deletion candidate only when all three hold:

1. `simctl` reports it `deletable` (not pinned inside an installed Xcode).
2. No installed Xcode SDK resolves to its build. Verify per installed Xcode:

   ```bash
   DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
     xcrun simctl runtime match list
   ```

   The `Chosen Runtime` lines list the builds SDKs actually use. Two formats
   appear: `<name> (<version> - <build>) - <identifier>` when an installed
   runtime matched, or a bare SDK-default build when none did. A bare build
   that matches no installed runtime still protects every installed runtime of
   that platform and marketing version, because the SDK has nothing newer to
   fall back to.
3. A newer runtime supersedes it on the same platform — either a higher version,
   or the same marketing version whose sibling build is the one SDKs choose
   (the superseded-beta case).

Delete only through the supported command, after separate irreversible approval:

```bash
xcrun simctl runtime delete <UUID>
```

Devices tied to the runtime stop booting afterwards, and restoring the runtime
requires a multi-gigabyte download.

### Never touch runtime storage manually

Do not manually delete:

- `/Library/Developer/CoreSimulator`
- `/System/Library/AssetsV2`
- Mounted runtime volumes

Do not disable SIP to remove runtime assets. If a mounted volume under
`/Library/Developer/CoreSimulator/Volumes` is not reported by
`simctl runtime list`, run `xcrun simctl runtime scan-and-mount` so CoreSimulator
re-registers it, then delete by UUID through `simctl`.

## Simulator dyld caches

`~/Library/Developer/CoreSimulator/Caches/dyld` keeps per-runtime shared caches.
Entries whose runtime identifier and build no longer match an installed runtime
are regenerable leftovers; matching caches rebuild automatically on next boot, so
avoid clearing caches for runtimes still in daily use.

## XCTest device clones

`~/Library/Developer/XCTestDevices` holds simulator clones created for parallel
test runs. They are recreated on the next test run, but never remove them while
tests are executing.

## Previews

SwiftUI Previews can use a separate simulator set. Inventory it explicitly before
suggesting:

```bash
xcrun simctl --set previews list
```

Deleting preview devices still loses state and requires itemized approval.
