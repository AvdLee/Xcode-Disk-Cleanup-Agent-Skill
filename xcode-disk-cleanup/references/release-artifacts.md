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
architectures. Reconnecting a compatible device may regenerate support, but old
symbols may be impossible to recover.

Treat DeviceSupport as preserve-by-default unless:

- The exact OS/device is no longer supported
- Historical symbolication is not required
- The user accepts regeneration or permanent loss

Never represent its entire size as an automatic cleanup opportunity.
