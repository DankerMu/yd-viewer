## Why
Issue #63's name-based regular-file check followed by a blocking open can hold the run lock indefinitely after replacement with a FIFO. The user explicitly replaces the old following-symlink proposal with no-follow reads.

Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent (expanded: readers/path safety/resource lifetime)
Blast radius: state/frontier/raw admission and all cfg.ic path readers
Selected risk packs: public readers, file IO, concurrency, resources, compatibility, errors, documentation
Evidence floor: bounded child-process FIFO replacement red/green, symlink/regular identity replacement, normal content and existing budgets, producer full suite

## What Changes
- Reuse existing safe_fs.open_file_no_follow (O_NOFOLLOW + O_NONBLOCK, post-open fstat regular and identity check); do not add a duplicate primitive or weaken it.
- Migrate controller._read_header_line, cfg_ic._read_bytes_limited and rawscan._is_readable to this descriptor-bound path.
- BREAKING: read paths no longer accept symlink leaves or ancestors; original #63 following-symlink clauses are superseded by the user's explicit ruling. #110 canonicalizes only configured yd_root, not raw/scratch or arbitrary parse paths.
- Preserve each caller's error classification, byte limits and parser/header semantics.

## Capabilities
### Modified Capabilities
- state-tools: no-follow bounded cfg.ic path reads.
- run-controller: descriptor-bound nonblocking state-header reads.
- raw-scan: no-follow readability classification after discovery.

## Impact
Only three read owners and affected tests/caller fixtures; docs-first inventory registration on packages/common/state_qc.py source row. No schema, scientific, scheduler, write/delete or safe_fs algorithm change.
