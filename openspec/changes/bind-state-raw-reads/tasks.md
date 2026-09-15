## 1. Implementation
- [ ] 1.1 Reuse the existing safe_fs open primitive in all three readers with exact descriptor ownership.
- [ ] 1.2 Preserve streaming/bounded content behavior and classify new no-follow errors at existing public boundaries.
- [ ] 1.3 Migrate symlink-following tests/callers and add bounded FIFO race, identity and resource-lifetime regressions.

## 2. Verification
- [ ] 2.1 Run red/green isolated FIFO and descriptor replacement proofs plus normal-reader smoke.
- [ ] 2.2 Run producer suite, Ruff, size guard, OpenSpec and CI; close cross-review findings.

## Risk packs
- Public API / CLI / script entry: selected - parse/frontier/judge refusals and indirect init caller; tasks1.2,1.3,2.1.
- Config / project setup: not selected - #110 complete; no further config domains or normalization.
- File IO / path safety / overwrite: selected - same-fd no-follow FIFO/identity races; tasks1.1,1.3,2.1.
- Schema / columns / units / field names: not selected - parsed bytes/format and raw rules unchanged.
- Auth / permissions / secrets: not selected - no permission model or secrets change; access-error classification covered by errors.
- Concurrency / shared state / ordering: selected - stat/open replacement while flock held; task2.1; no lock implementation change.
- Resource limits / large input / discovery: selected - FD lifetime, streaming/header/IC caps; tasks1.1,1.2,1.3,2.2.
- Legacy compatibility / examples: selected - intentional symlink-following removal, valid physical paths and byte roundtrip retained; tasks1.2,1.3.
- Error handling / rollback / partial outputs: selected - ValueError/STATE_UNREADABLE/raw three states, no partial writes; tasks1.2,2.1.
- Release / packaging / dependency compatibility: not selected - stdlib/existing helper only, no dependencies.
- Documentation / migration notes: selected - docs-first no-follow ruling and source-row inventory; task1.3 and review. Historical archive is not rewritten as an active fixture.
