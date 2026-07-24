# Generated data

## DerivedData

DerivedData contains build products, indexes, logs, macro/plugin products, and
package checkouts. It is regenerable, but rebuilding can be expensive.

Strong candidates:

- The recorded `WorkspacePath` no longer exists
- The folder contains only package downloads and no build products
- A project-local DerivedData folder belongs to completed work
- The user explicitly accepts rebuilding and reindexing

Protect:

- Active workspaces and builds
- Custom DerivedData whose owner is unknown
- Shared module caches during concurrent agent activity

Xcode may use a custom path from Settings → Locations. Do not assume every
DerivedData directory is below the default home directory.

## Project `.build`

SwiftPM and project scripts frequently place large generated folders beside source.
Scan only roots the user authorizes. Exclude dependency checkouts to avoid counting
nested builds multiple times.

Prefer supported scoped commands when available:

```bash
swift package clean
swift package reset
swift package purge-cache
```

Check `swift package --help` against the selected toolchain before recommending a
command. Never delete `Package.resolved`, mirrors, registries, security
configuration, or credentials.

## CocoaPods cache

`~/Library/Caches/CocoaPods` stores pod downloads that `pod install` re-fetches on
demand. It is regenerable and safe to Trash. Project `Pods/` directories are part
of the working tree and are never cleanup candidates.

## Documentation

Downloaded documentation is usually recoverable, but manually imported DocSets may
be unique. Only classify versioned Xcode cache folders as regenerable when a newer
sibling exists. Never remove documentation from inside an Xcode application.

## Cost language

Avoid saying “safe to delete” without qualification. State the practical cost:

- Full rebuild/reindex
- Package or binary artifact downloads
- Loss of offline documentation
- Temporary failures for concurrent builds
