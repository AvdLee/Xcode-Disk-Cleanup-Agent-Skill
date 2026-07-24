# Contributing

Contributions should make Xcode storage cleanup more explainable and safer.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall xcode-disk-cleanup/scripts tests
```

## Requirements

- Use Python’s standard library unless a dependency has a strong justification.
- Keep `audit` read-only.
- Require exact candidate IDs and explicit confirmation for all mutations.
- Add deterministic tests for every new category or safety guard.
- Preserve archives, dSYMs, signing material, and uncertain assets by default.
- Prefer first-party Apple or Swift references.
- Never make a new cleanup category actionable solely because it is large or old.

Open a pull request describing the evidence, safety boundary, regeneration cost,
and verification strategy.
