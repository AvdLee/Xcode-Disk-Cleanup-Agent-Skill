# Archives, dSYMs, and DeviceSupport

## Archives and dSYMs

Archives are not ordinary caches. A distributed archive contains the exact binary
and matching dSYM UUIDs needed to diagnose crashes for that build. Rebuilding the
same source does not recreate a compatible dSYM.

Preserve by default:

- Any App Store, TestFlight, enterprise, notarized, or externally distributed build
- Any archive whose distribution status is unknown
- Any archive without a verified immutable backup of the archive and dSYMs

Matching app version and build numbers do not prove two archives are interchangeable.
Only propose deletion when the user confirms the build was not distributed or the
exact artifact is safely backed up.

Prefer review in Xcode Organizer. The scanner reports archive metadata but marks
archives `report-only`.

Apple references:

- https://developer.apple.com/documentation/xcode/building-your-app-to-include-debugging-information
- https://developer.apple.com/documentation/xcode/adding-identifiable-symbol-names-to-a-crash-report

## DeviceSupport

DeviceSupport contains symbols and support data tied to device OS releases and
architectures. Every platform keeps its own folder: `iOS DeviceSupport`,
`watchOS DeviceSupport`, `tvOS DeviceSupport`, and `visionOS`/`XROS`
`DeviceSupport` under `~/Library/Developer/Xcode/`.

The scanner keeps the newest symbol set per platform and proposes older versions
for Trash. This mirrors what developers expect: symbols for OS builds their
devices no longer run rarely earn their multi-gigabyte footprint. Still treat the
proposal as destructive, because:

- Reconnecting a device regenerates support only for OS builds still in use
- Symbolicating a crash from a retired OS build needs that exact symbol set
- Old symbols may be impossible to download again

Confirm the user no longer symbolicates crashes from those OS versions before
approving.

## Diagnostic logs

`~/Library/Logs/CoreSimulator` and `~/Library/Developer/Xcode/iOS Device Logs`
grow without bound and regrow automatically. Safe to Trash unless an active bug
investigation depends on historical logs.

## Legacy DocSets

`~/Library/Developer/Shared/Documentation/DocSets` holds documentation bundles
from the era of downloadable DocSets. Current Xcodes no longer distribute them,
and removed DocSets may be impossible to download again — confirm no offline
documentation workflow depends on them before approving.
