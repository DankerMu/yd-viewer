## 1. Evidence closure
- [x] 1.1 Add one test importing ERROR_KINDS and asserting exact frozenset of nine literals from m2 fixture.
- [ ] 1.2 Parent proves new test green, added-tenth mutation red and renamed-literal mutation red in fresh uv interpreters; full profile matrix and Ubuntu CI green.
- [x] 1.3 Retire vocabulary Known-limit without changing admission fallback policy or runtime source.
## Risk packs
- Public API / CLI / script entry: selected only as unchanged oracle — literal equality and two mutation proofs.
- Config / project setup: not selected — untouched.
- File IO / path safety / overwrite: not selected — no runtime edit.
- Schema / columns / units / field names: not selected — no schema edit.
- Auth / permissions / secrets: not selected — untouched.
- Concurrency / shared state / ordering: not selected — no runtime edit; mutation isolated per process.
- Resource limits / large input / discovery: not selected — untouched.
- Legacy compatibility / examples: not selected — existing behavior unchanged, full suite evidence.
- Error handling / rollback / partial outputs: not selected — no error behavior change.
- Release / packaging / dependency compatibility: not selected — untouched.
- Documentation / migration notes: selected — m2 Known-limit now points to exact-set evidence.
- Geospatial / CRS / shapefile sidecars: not selected — untouched.
- Time series / forcing / temporal boundaries: not selected — untouched.
- 状态链 / warm-start 定戳一致性: not selected — untouched.
- NWM 快照溯源与 DB-free 隔离: not selected — untouched.
Seam: exported ERROR_KINDS, exactly as issue76 requests. Phase2 clean audit permits review-not-required track for none tier; no runtime changes may use that exception.
