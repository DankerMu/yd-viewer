## 1. Fixture
- [ ] 1.1 Register inventory deviation before implementation and obtain fixture-review pass / strict validation.
## 2. Repair and verification
- [ ] 2.1 Prove four-walker defect red with real-fd injection including consumed/unconsumed previous and bounded repetition.
- [ ] 2.2 Apply uniform ownership handoff and primary-preserving cleanup; show regressions green and successful root/deep/list behavior.
- [ ] 2.3 Run producer uv pytest, ruff check/format and line guard; OpenSpec all/strict validation.
## 3. Delivery artifacts
- [ ] 3.1 Complete cross-review and same-PR archive/spec synchronization; merge remains external SHA-bound gate after final CI.
## Risk pack mapping
- Selected Public API / CLI / script entry: unchanged callers and returned-fd/list semantics (2.2–2.3).
- Selected File IO / path safety / overwrite: four handoffs, no-follow/containment/refusals retained (2.1–2.3).
- Selected Resource limits / large input / discovery: bounded repeated failures and one-shot successor cleanup (2.1).
- Selected Error handling / rollback / partial outputs: previous primary, secondary cleanup OSError and no retries (2.1–2.2).
- Selected Documentation / migration notes: inventory and archived spec (1.1,3.1).
- Not selected Concurrency / shared state / ordering: no new concurrency; fd transfer ordering covered under File IO.
- Not selected Auth / permissions / secrets: no policy changes; existing filesystem admission retained.
- Not selected Legacy compatibility / examples: no migration; API preservation and full suite cover current consumers.
- Not selected Config / project setup, Schema / columns / units / field names, Release / packaging / dependency compatibility: untouched.
