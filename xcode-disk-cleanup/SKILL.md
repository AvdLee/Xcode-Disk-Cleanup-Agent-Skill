---
name: xcode-disk-cleanup
description: >-
  Audit and safely clean Xcode-related developer storage on macOS. Use whenever a
  developer mentions low disk space, Xcode storage, DerivedData, simulator devices
  or runtimes, DeviceSupport, archives, dSYMs, SwiftPM caches, project .build
  folders, downloaded Xcode DMGs/XIPs, duplicate Xcode installers, or old Xcode
  applications. Always measure first, report recoverable GiB with evidence, and
  obtain explicit itemized approval before any mutation.
---

# Xcode Disk Cleanup

## Safety contract

- Treat every cleanup request as permission to audit, not permission to delete.
- Never mutate storage before presenting exact candidate IDs, paths or simulator
  identifiers, measured sizes, risks, and regeneration costs.
- Ask the user to approve specific IDs. Broad replies such as “clean Xcode” or
  “delete everything” are not itemized approval.
- Default ordinary files and directories to Trash. Explain that space is not
  reclaimed until Trash is emptied, and request separate approval before doing so.
- Revalidate identity and running-process usage immediately before mutation.
- Never use wildcards, unbounded `rm -rf`, `sudo`, SIP changes, or manual deletion
  inside system-managed CoreSimulator and MobileAsset directories.
- Preserve archives and dSYMs unless the user proves the exact distributed build
  is backed up elsewhere and separately approves deletion.
- Never delete `Package.resolved`, signing assets, credentials, custom DocSets, the
  active Xcode, a running simulator, or an Xcode that uniquely provides a required
  SDK/toolchain.
- Report summed candidate sizes separately from actual APFS space recovered.

Read `references/safety-model.md` before applying cleanup.

## Workflow

### 1. Preflight

1. Confirm the host is macOS and the bundled script is available.
2. Check for active Xcode, Simulator, `xcodebuild`, Swift build, test, archive, and
   package-resolution processes.
3. If builds are active, audit may continue, but defer all cleanup.
4. Determine optional source roots for project-local `.build` and `.derived-data`
   scanning. Do not crawl the whole home directory.

### 2. Run the read-only audit

```bash
python3 "${SKILL_DIR}/scripts/xcode_disk_cleanup.py" audit \
  --output-dir .xcode-disk-cleanup-audit \
  [--scan-root /path/to/projects]
```

Read both generated files:

- `.xcode-disk-cleanup-audit/report.md`
- `.xcode-disk-cleanup-audit/audit.json`

The script audits DerivedData, compiler caches, documentation caches, SwiftPM,
simulator devices, Xcode installers, installed Xcodes, archives, DeviceSupport,
and explicitly requested project roots.

### 3. Validate and enrich findings

Use the category router:

| Finding | Reference |
|---|---|
| DerivedData, module caches, project `.build` | `references/generated-data.md` |
| Simulator devices and runtimes | `references/simulators.md` |
| Archives, dSYMs, DeviceSupport | `references/release-artifacts.md` |
| Xcode DMGs/XIPs and installed Xcodes | `references/xcode-installations.md` |
| Confirmation and deletion behavior | `references/safety-model.md` |

Do not mechanically accept a scanner classification. Verify uncertain ownership,
custom paths, backups, and toolchain requirements.

### 4. Present the proposal

Sort by recoverable GiB and show:

1. Candidate ID
2. Category and exact path/identifier
3. Recoverable GiB
4. Risk: regenerable, destructive, or preserve
5. Evidence
6. What must be rebuilt, redownloaded, or permanently lost
7. Proposed action

Include:

- Total candidate GiB by risk
- Current free disk space
- A warning that APFS cloning and snapshots make candidate sizes non-additive
- A direct request for approval of exact IDs

### 5. Apply only approved IDs

Invoke apply only after the user explicitly approves the exact IDs shown in the
current audit:

```bash
python3 "${SKILL_DIR}/scripts/xcode_disk_cleanup.py" apply \
  --audit-file .xcode-disk-cleanup-audit/audit.json \
  --ids "<approved-id-1>" "<approved-id-2>" \
  --confirm I_APPROVED_THE_SELECTED_ITEMS \
  --output .xcode-disk-cleanup-audit/cleanup-result.json
```

Simulator deletions are irreversible and require a second approval plus:

```text
--confirm-irreversible I_APPROVED_IRREVERSIBLE_SIMULATOR_DELETION
```

Do not pass either confirmation phrase unless the corresponding user approval is
present in the conversation.

### 6. Verify

1. Re-run the audit.
2. Compare `df` free space before and after.
3. Distinguish “moved to Trash” from immediately reclaimed space.
4. Confirm protected and unapproved candidates remain.
5. Report successes, failures, and deferred items.

## Xcode installer and application rules

- Search `.xip`, `.dmg`, and `.zip` installers through Spotlight and common
  download folders.
- Suggest an installer only when its parsed version matches an installed Xcode;
  otherwise report it as uncertain.
- An older Xcode may only be suggested, never automatically selected, when it is
  not active/running and a newer same-channel installation exists.
- Spotlight `kMDItemLastUsedDate` reflects LaunchServices opens, not command-line
  use. Combine it with `xcode-select`, `DEVELOPER_DIR`, running processes,
  `.xcode-version`, CI scripts, and unique SDK/toolchain evidence.
- Never infer that a stable Xcode supersedes a beta, or vice versa.

## Output quality checklist

- [ ] Audit completed without mutation
- [ ] Every item has an exact size and stable ID
- [ ] Candidate and actual recovered space are separate
- [ ] Archives/dSYMs are preserve-by-default
- [ ] Active processes and selected Xcode are protected
- [ ] User approved exact IDs
- [ ] Irreversible operations received separate approval
- [ ] Post-cleanup audit confirms only approved items changed
