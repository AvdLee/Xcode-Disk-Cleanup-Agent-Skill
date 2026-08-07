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
Only propose deletion when the archive was provably never distributed or the exact
artifact is safely backed up.

### Distribution evidence

Xcode Organizer records every upload and export in the archive's `Info.plist`
under the `Distributions` key (destination, event date, state). The scanner uses
this to split archives into three classes:

- **Distributed** (`Distributions` present): preserve, report-only. Its dSYMs may
  be required to symbolicate crashes from the shipped build.
- **Orphan** (never distributed and a newer archive of the same app exists):
  proposed for Trash. Nothing was shipped from it, so no crash report can ever
  need its dSYMs; the newer archive covers the "I still have an archive" comfort.
- **Newest undistributed**: preserve, report-only — it may be pending
  distribution.

The `Distributions` record only reflects this Mac. If the user distributes the
same archives from another machine or CI, confirm before approving orphan
archives.

Prefer review in Xcode Organizer for anything uncertain.

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
