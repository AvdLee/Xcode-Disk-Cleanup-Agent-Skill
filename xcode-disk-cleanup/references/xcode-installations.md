# Xcode installers and installations

## Installer discovery

Search Spotlight plus bounded common locations for:

- `Xcode*.xip`
- `Xcode*.dmg`
- `Xcode*.zip`

Do not recursively crawl an entire home directory. Report mounted disk images and
detach them before an approved move to Trash.

An installer is a strong candidate only when:

1. Its filename or verified metadata identifies an Xcode version.
2. An installed application has the same version.
3. The user accepts losing offline reinstall access.

Filename parsing is evidence, not proof. Unknown or beta naming stays report-only.

## Installed Xcodes

Inventory applications and versions with:

```bash
mdfind 'kMDItemCFBundleIdentifier == "com.apple.dt.Xcode"'
xcode-select --print-path
env DEVELOPER_DIR="/path/to/Xcode.app" xcodebuild -version
env DEVELOPER_DIR="/path/to/Xcode.app" xcodebuild -showsdks
mdls -name kMDItemLastUsedDate "/path/to/Xcode.app"
```

Evidence priority:

1. Running processes and the active `xcode-select` path
2. Current `DEVELOPER_DIR`
3. `.xcode-version`, CI, scripts, and project requirements
4. SDKs/toolchains uniquely supplied by the installation
5. Spotlight last-used metadata
6. Filesystem modification date

Spotlight last-used metadata records LaunchServices opens and can miss command-line
or CI usage. Modification time usually reflects installation or updates, not use.

## Suggestion threshold

Suggest—not select—an older Xcode only when:

- It is not active or running
- A newer installation in the same stable/beta channel exists
- No unique SDK/toolchain requirement is found
- No recent project or automation reference is found
- The evidence and uncertainty are shown to the user

Never assume a stable Xcode replaces a beta or that a beta replaces a stable release.
Xcode application removal always requires separate explicit approval.

Apple reference:

- https://developer.apple.com/documentation/xcode/configuring-command-line-tools-settings
