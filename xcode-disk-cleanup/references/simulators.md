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

Manage runtimes through Xcode Settings → Components. Xcode reports recoverable
storage and maintains system registration correctly.

Do not manually delete:

- `/Library/Developer/CoreSimulator`
- `/System/Library/AssetsV2`
- Mounted runtime volumes

Do not disable SIP to remove runtime assets. If Xcode leaves protected orphaned
assets, direct the user to supported Xcode/macOS remediation.

## Previews

SwiftUI Previews can use a separate simulator set. Inventory it explicitly before
suggesting:

```bash
xcrun simctl --set previews list
```

Deleting preview devices still loses state and requires itemized approval.
