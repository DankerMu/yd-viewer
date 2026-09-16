## Why
Issue #232 is reproduced: a secondary KeyboardInterrupt from file close escapes the opener cleanup loop, replaces the validation primary and skips parent cleanup.

## Triage
Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent (expanded: file IO and ownership)
Blast radius: pre-return descriptor cleanup and exception causality in open_file_no_follow
Selected risk packs: File IO / path safety / overwrite; Error handling / rollback / partial outputs
Evidence floor: real-fd regression red before / green after; producer suite, lint, format, line guard; strict OpenSpec validation; CI

## What Changes
- Isolate secondary BaseException at each opener close call, preserve original primary and independently attempt remaining parent cleanup.
- Add focused real-fd regression coverage without retry or failed-close liveness assumptions.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `unreturned-file-descriptor-ownership`: explicitly require secondary interruption isolation.

## Impact
Only opener cleanup and its ownership tests; shared close helper and downstream readers keep their contracts. No configuration or public signature change.
