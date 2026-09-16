## Why
Issue #44: states probe permissions/IO failures must be guard refusals with actionable path and errno, not escaping exceptions or generic runtime failures.
M2 stays archived; this independent fixture follows #95 merged and rebased on master.

## What Changes
- Classify PermissionError and other OSError at _check_states_dir, including root metadata and one-level source traversal.
- Preserve #95 any-source file presence, read-only behavior and existing missing/non-directory diagnosis.

## Capabilities
### Modified Capabilities
- `cli-config`: state-probe error classification at guard boundary.

## Impact
Only cli.py and corresponding tests; no main-wide exception policy or NWM interpreter change.
Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent (CLI, permissions, file IO triggers)
Blast radius: run startup refusal reason and guard exit code.
Selected risk packs: Public API / CLI / script entry; File IO / path safety / overwrite; Auth / permissions / secrets; Error handling / rollback / partial outputs; Legacy compatibility / examples; Documentation / migration notes
Evidence floor: baseline red/fixed green CLI seam tests, actual mode-000 refusal, producer suite/ruff, strict/all OpenSpec validation.
