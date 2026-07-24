# Safety model

## Permission boundary

A request to inspect, audit, find space, or clean Xcode authorizes read-only
discovery. Mutation begins only after the agent presents a current itemized plan
and the user approves exact candidate IDs.

Approval expires when:

- The candidate path, inode, ownership, or purpose changes
- A build or simulator starts using the candidate
- The audit is no longer current enough to trust
- The proposed action changes from Trash to permanent deletion
- A reversible action changes into an irreversible API operation

## Risk levels

### Regenerable

Generated build products, indexes, package downloads, and caches. These still
require confirmation because deletion can trigger hours of rebuilding or downloads
and can disrupt other agents.

### Destructive

Simulator devices, installers, DeviceSupport, and Xcode applications. Data may be
redownloadable, but local state or offline recovery is lost.

### Preserve

Archives, dSYMs, unknown documentation, active Xcodes, unique toolchains, signing
assets, credentials, and anything with uncertain ownership or recovery.

## Mutation rules

- Prefer supported Xcode, SwiftPM, and `simctl` commands.
- Move ordinary paths to Trash; never follow symlinks.
- Do not empty Trash without separate confirmation.
- Reject wildcard IDs and broad path prefixes.
- Recheck `ps` for Xcode, Simulator, builds, tests, archives, and package resolution.
- Refuse a path when its inode/device no longer matches the audit.
- Avoid `sudo`; do not disable SIP or alter protected system assets.
- Do not manually remove `/Library/Developer/CoreSimulator`,
  `/System/Library/AssetsV2`, or an entire CoreSimulator device set.

## APFS accounting

`du` measures logical blocks reachable through a path. APFS clones, snapshots, and
shared volumes mean candidate sizes are not necessarily additive. Measure actual
free-space change with `df` after cleanup and state when files remain in Trash.
