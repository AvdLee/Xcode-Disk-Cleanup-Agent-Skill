# Agent guidance

This repository contains a safety-sensitive Agent Skill.

- Keep all audits read-only.
- Never weaken exact-ID or confirmation checks.
- Ordinary files move to Trash; permanent deletion is not a default.
- Simulator deletion must use `simctl` and separate irreversible confirmation.
- Archives and dSYMs remain preserve-by-default.
- Add deterministic tests for changed classification or mutation behavior.
- Do not introduce wildcard deletion, `sudo`, SIP changes, or direct removal of
  system-managed Simulator runtime assets.
- Run `python3 -m unittest discover -s tests -v` before completing changes.
