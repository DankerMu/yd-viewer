## 1. Implementation
- [x] 1.1 Normalize LocalConfig.yd_root once, validate absolute spelling and classify resolution failures.
- [x] 1.2 Migrate every root consumer and remove stale alias-rejection comments; keep safe_fs unchanged.
- [x] 1.3 Add discriminating alias/retarget/descendant/error regressions and migrate changed Path/error contracts.

## 2. Verification
- [x] 2.1 Demonstrate red baseline and green alias bootstrap, retarget safety, invalid roots, and internal symlink refusal.
- [ ] 2.2 Run producer pytest, ruff, OpenSpec and actual CLI smoke; complete cross-review and CI.

## Risk packs
- Public API / CLI / script entry: selected - Path representation and early errors; tasks 1.2, 1.3, 2.2.
- Config / project setup: selected - constructor/load normalization; tasks 1.1, 2.1.
- File IO / path safety / overwrite: selected - alias retarget and descendant refusal; tasks 1.3, 2.1.
- Schema / columns / units / field names: not selected - TOML keys and persisted formats unchanged; Path API covered above.
- Auth / permissions / secrets: not selected - no access policy or secrets change.
- Concurrency / shared state / ordering: selected - alias changes after load; task 2.1, no new concurrency protocol.
- Resource limits / large input / discovery: not selected - no new reader or discovery budget.
- Legacy compatibility / examples: selected - direct constructors and entrypoints; tasks 1.3, 2.2.
- Error handling / rollback / partial outputs: selected - ConfigError before writes, unchanged root absence policy; tasks 1.1, 2.1.
- Release / packaging / dependency compatibility: not selected - stdlib only, dependency files unchanged.
- Documentation / migration notes: selected - docs-first root contract and #32 ownership note here; task 1.2 and review.
