## 1. Implementation
- [ ] 1.1 Add allow_nan=False to _render_manifest without new exception branch or kind.
- [ ] 1.2 Add stage_raw cases for carried NaN/+inf/-inf -> source-manifest and unchanged recursive work/source snapshots; finite nested filter value -> strict parser accepts exact preserved value.
- [ ] 1.3 Retire m2 task Known-limit for nonfinite output only; preserve other deferred policies.
## 2. Verification
- [ ] 2.1 Parent baseline/removal proof makes nonfinite cases fail; fixed focused cases and producer/viewer matrix pass.
- [ ] 2.2 Ruff check/format, OpenSpec strict/all, stage log and Ubuntu CI pass; no macOS CI.
## Risk packs
- Public API / CLI / script entry: selected — stage_raw typed failure.
- Config / project setup: not selected — unchanged.
- File IO / path safety / overwrite: selected — refusal snapshots and admission-before-write; existing writers unchanged.
- Schema / columns / units / field names: selected — strict JSON positive parse and finite value preservation.
- Auth / permissions / secrets: not selected — unchanged.
- Concurrency / shared state / ordering: selected — pre-copy serialization and existing claim admission regression; no concurrency protocol change.
- Resource limits / large input / discovery: not selected — no new traversal beyond serializer.
- Legacy compatibility / examples: selected — valid GFS/IFS outputs unchanged.
- Error handling / rollback / partial outputs: selected — existing source-manifest branch, zero work writes.
- Release / packaging / dependency compatibility: not selected — unchanged.
- Documentation / migration notes: selected — retire exact known limit.
- Geospatial / CRS / shapefile sidecars: not selected — untouched.
- Time series / forcing / temporal boundaries: not selected — values unrelated.
- 状态链 / warm-start 定戳一致性: not selected — untouched.
- NWM 快照溯源与 DB-free 隔离: selected — immutable source snapshots.
Seam: issue #75 public stage_raw; required evidence exact inputs above, profile matrix commands via uv only.
