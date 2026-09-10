## Why
Issue #71: pre-existing symlink descendants redirect temporary raw copies outside their disposable work root. Dependency #26 is closed; controller already claims fresh exact work before staging.

## What Changes
- Reject symlink components below the caller-provided work root before any staging writes, using existing error kinds.
- Preserve a symlinked work root itself and existing no-overwrite/source rules.

## Capabilities
### New Capabilities
- `raw-staging-target-containment`: preflight rejects symlink descendants without filesystem changes.
### Modified Capabilities
None.

## Impact
Only rawcopy.py and test_rawcopy.py implementation ownership; no controller/API/schema/dependency/CI changes.
