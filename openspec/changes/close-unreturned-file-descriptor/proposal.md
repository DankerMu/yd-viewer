## Why
Issue #225 identifies an acquired file descriptor leaking when post-open validation raises BaseException. Cleanup must not replace the original failure.

Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent (expanded: shared file IO entrypoint)
Blast radius: safe_fs consumers including object store, bounded readers, assembly, publisher, tracker and NetCDF reader.
Selected risk packs: Public API; File IO; Resource limits; Error handling; Documentation / migration notes
Evidence floor: real-fd targeted red/green regressions, producer pytest/ruff, strict OpenSpec validation.

## What Changes
- Retain file ownership until validation and parent cleanup complete; close unreturned file descriptors once on BaseException.
- Preserve primary exception identity and record cleanup failures secondarily without retry.
- Register the local fork in the authorized snapshot inventory before source edits.

## Capabilities
### New Capabilities
- `unreturned-file-descriptor-ownership`: pre-return descriptor cleanup and exception precedence.
### Modified Capabilities
None.

## Impact
Only safe_fs.open_file_no_follow, local regression tests and workflow artifacts. No signature, format, dependency or caller migration.
