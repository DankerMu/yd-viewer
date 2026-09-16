## Why
Issue #95: empty source directories currently masquerade as a bootstrapped state chain.
M2 is archived; this independent fixture supersedes only its stale run-stub wording, not archived tasks.

## What Changes
- Detect a state file one level below states rather than any top-level entry.
- Preserve read-only detection and production delegation when any source has a state file.

## Capabilities
### Modified Capabilities
- `cli-config`: clarify run bootstrap guard emptiness.

## Impact
Only cli.py and corresponding CLI tests; no init/controller changes.
Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent (CLI and file IO triggers)
Blast radius: run startup admission and operator diagnosis.
Selected risk packs: Public API / CLI / script entry; File IO / path safety / overwrite; Error handling / rollback / partial outputs; Legacy compatibility / examples; Documentation / migration notes
Evidence floor: CLI seam red/green regressions, producer pytest/ruff, OpenSpec validation.
