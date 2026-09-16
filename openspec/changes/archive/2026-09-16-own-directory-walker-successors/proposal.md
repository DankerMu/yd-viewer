## Why
Issue #183: four walkers acquire a successor before closing previous, but do not record ownership until that close succeeds. Close failure leaks successor and may retry previous whose state is unknown.

Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent (expanded: shared file IO/path safety)
Blast radius: parent, directory-open and listing consumers of safe_fs.
Selected risk packs: Public API; File IO; Resource limits; Error handling; Documentation
Evidence floor: four-walker real-fd red/green regressions, repeated failures, producer pytest/ruff/line guard, OpenSpec strict/all.

## What Changes
- Uniform previous/current ownership transfer before previous close.
- Successor cleanup once on failure; previous never retried; preserve primary/cause and root cleanup.
- Register local fork in authorized inventory; keep #225 and #55/#122 behavior.

## Capabilities
### New Capabilities
- `directory-walker-successor-ownership`: directory handoff cleanup on previous-close failures.
### Modified Capabilities
None.

## Impact
safe_fs.py four walkers and local regression tests only; no call signatures, formats or dependencies change.
