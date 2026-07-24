# Xcode Disk Cleanup Agent Skill

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An audit-first Agent Skill for reclaiming Xcode development storage without
surprising deletions. It measures cleanup opportunities, explains the cost of each
one, reports recoverable GiB, and waits for explicit itemized approval before
changing anything.

Building iOS apps with AI agents? [RocketSim](https://www.rocketsim.app/) gives
agents fast, structured access to inspect and interact with iOS Simulator apps.

## What it audits

- Xcode DerivedData and shared compiler caches
- Project-local `.build` and `.derived-data` folders
- Available and unavailable Simulator devices
- Installed Simulator runtimes
- SwiftUI Preview device sets
- SwiftPM download caches
- Documentation caches
- Archives, dSYMs, and DeviceSupport
- Downloaded Xcode `.xip`, `.dmg`, and `.zip` installers
- Installed Xcode applications, active selection, and evidence of recent use

## Safety guarantee

The skill treats “clean Xcode” as permission to inspect, not delete.

Before any mutation, it presents:

- A stable candidate ID
- Exact path or Simulator identifier
- Measured bytes and GiB
- Risk and regeneration cost
- Evidence supporting the recommendation
- The exact action it proposes

The user must approve exact IDs. Ordinary files move to Trash by default.
Irreversible Simulator operations require a second confirmation. Archives and
dSYMs are preserved unless the exact distributed build is safely backed up.

## Installation

### Agent Skills-compatible tools

```bash
npx skills add https://github.com/AvdLee/Xcode-Disk-Cleanup-Agent-Skill \
  --skill xcode-disk-cleanup
```

### Claude Code plugin

```text
/plugin marketplace add AvdLee/Xcode-Disk-Cleanup-Agent-Skill
/plugin install xcode-disk-cleanup@xcode-disk-cleanup-agent-skill
```

### Cursor

This repository includes `.cursor-plugin/plugin.json` and follows Cursor’s plugin
and Agent Skills layout.

### Codex

Copy or symlink `xcode-disk-cleanup/` into your Codex skills directory. The
repository includes `agents/openai.yaml`.

### pi

```bash
pi install https://github.com/AvdLee/Xcode-Disk-Cleanup-Agent-Skill
```

## Usage

Ask your agent:

> Audit my Xcode-related disk usage and show how many GB each cleanup could recover.

The agent runs a read-only audit:

```bash
python3 xcode-disk-cleanup/scripts/xcode_disk_cleanup.py audit \
  --output-dir .xcode-disk-cleanup-audit \
  --scan-root ~/Developer
```

It then presents an itemized report and waits. Approved ordinary files move to
Trash; space is not claimed as recovered until measured with `df`.

## What makes this different

- Read-only by default
- Deterministic JSON and Markdown reports
- APFS-aware candidate versus actual recovery accounting
- Supported `simctl`, SwiftPM, and Xcode workflows
- Evidence-based Xcode installer matching
- Multi-signal suggestions for potentially unused Xcode versions
- Path identity and running-process revalidation before cleanup
- No wildcard cleanup, `sudo`, SIP changes, or raw system-runtime deletion

## Repository structure

```text
xcode-disk-cleanup/
  SKILL.md
  references/
    generated-data.md
    release-artifacts.md
    safety-model.md
    simulators.md
    xcode-installations.md
  scripts/
    xcode_disk_cleanup.py
tests/
evals/
.claude-plugin/
.cursor-plugin/
agents/
```

## See also my other skills

- [SwiftUI Expert](https://github.com/AvdLee/SwiftUI-Agent-Skill)
- [Swift Concurrency Expert](https://github.com/AvdLee/Swift-Concurrency-Agent-Skill)
- [Swift Testing Expert](https://github.com/AvdLee/Swift-Testing-Agent-Skill)
- [Core Data Expert](https://github.com/AvdLee/Core-Data-Agent-Skill)
- [Xcode Build Optimization](https://github.com/AvdLee/Xcode-Build-Optimization-Agent-Skill)
- [RocketSim Agent Skill](https://www.rocketsim.app/docs/features/agentic-development/agent-skill/)

## Sources

The safety model prioritizes first-party guidance:

- [Managing Xcode components](https://developer.apple.com/documentation/xcode/downloading-and-installing-additional-xcode-components)
- [Building with debugging information](https://developer.apple.com/documentation/xcode/building-your-app-to-include-debugging-information)
- [Symbolicating crash reports](https://developer.apple.com/documentation/xcode/adding-identifiable-symbol-names-to-a-crash-report)
- [Configuring command-line tools](https://developer.apple.com/documentation/xcode/configuring-command-line-tools-settings)
- [SwiftPM cleaning builds and caches](https://docs.swift.org/swiftpm/documentation/packagemanagerdocs/packageclean/)
- [APFS shared space](https://support.apple.com/guide/mac-help/mac-shares-space-apfs-volumes-sysp560a2952/mac)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Cleanup rules should favor evidence and
supported APIs over aggressive deletion.

## License

MIT. See [LICENSE](LICENSE).
