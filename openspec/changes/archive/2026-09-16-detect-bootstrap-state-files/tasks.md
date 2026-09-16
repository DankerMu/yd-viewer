## Risk packs
- Selected Public API / CLI / script entry: CLI red/green lanes below.
- Selected File IO / path safety / overwrite: one-level file boundary and read-only snapshots below; no new symlink policy.
- Selected Error handling / rollback / partial outputs: empty init-residue diagnosis; OSError classification explicitly deferred to #44.
- Selected Legacy compatibility / examples: correct historical staged-unimplemented assertion to current production delegate.
- Selected Documentation / migration notes: independent cli-config delta; no archived M2 tasks edits.
- Not selected Auth / permissions / secrets: orthogonal #44 next; no permission changes here.
- Not selected Resource limits / large input / discovery: keep early exit and fixed depth; no capacity policy.
- Not selected Concurrency / shared state / ordering: locking unchanged; no new synchronization.
- Not selected Config / project setup; Schema / columns / units / field names; Release / packaging / dependency compatibility: no changes.

## 1. Implementation
- [x] 1.1 Replace directory-entry emptiness with one-level state-file detection using STATE_SUFFIX, preserving any-source semantics.
- [x] 1.2 Add cli.main empty-source, false-positive and one/two-source regressions; deliberately correct existing production fixture with explanatory docstring; verify no writes or init calls.

## 2. Evidence and delivery
- [x] 2.1 Record new behavior red against original source and green against implementation; CLI smoke through main.
- [x] 2.2 Run producer uv run pytest, uv run ruff check ., uv run ruff format --check .; strict and all OpenSpec validation.
- [x] 2.3 Complete fixture/code reviews, CI and merge gate; archive this independent change and synchronize cli-config main spec, never restore M2 tasks. PR #229 merged at reviewed head ffcf93b6d6bec8bc7b5f3e82514f1425422040ab; archive and spec sync recorded in the follow-up commit.
