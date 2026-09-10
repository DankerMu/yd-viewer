## 1. Implementation
- [x] 1.1 Add Mapping/key/equality admission guard with source-manifest and actionable lead/variable/value diagnostic, no alias/overwrite.
- [x] 1.2 Add public mismatch (apcp 2t-WRONG and reverse), invalid filter shape, missing key and equal custom-name regressions with raw/work snapshots and exact preserved metadata.
- [x] 1.3 Align existing surrogate test input on both names, retaining assertions; verify #75 serializer and existing claim/time/accumulation tests remain discriminating.
- [x] 1.4 Publish six-key contract and replace #99 Known-limit without touching sibling policies.
## 2. Verification
- [x] 2.1 Baseline source against new negatives must fail; fixed focused and producer/viewer suites pass.
- [x] 2.2 Ruff check/format, OpenSpec strict/all, stage log and final Ubuntu CI pass, uv only.
## Risk packs
- Public API / CLI / script entry: selected — stage_raw typed failure and diagnostic.
- Config / project setup: not selected — untouched.
- File IO / path safety / overwrite: selected — source/work snapshot refusal; no new writer.
- Schema / columns / units / field names: selected — relation and shape rows, verbatim positive.
- Auth / permissions / secrets: not selected — untouched.
- Concurrency / shared state / ordering: selected — admission before copies; unchanged WorkClaim tests, no new protocol.
- Resource limits / large input / discovery: not selected — constant local check per existing entry.
- Legacy compatibility / examples: selected — accepted GFS/IFS metadata unchanged.
- Error handling / rollback / partial outputs: selected — all mismatch/shape errors source-manifest with no work writes.
- Release / packaging / dependency compatibility: not selected — untouched.
- Documentation / migration notes: selected — six-key requirement/known-limit update.
- Geospatial / CRS / shapefile sidecars: not selected — untouched.
- Time series / forcing / temporal boundaries: not selected — time checks unchanged.
- 状态链 / warm-start 定戳一致性: not selected — untouched.
- NWM 快照溯源与 DB-free 隔离: selected — no invented aliases and immutable source snapshots.
Seam: issue #99 stage_raw, not helper mocks. Required evidence uses existing project-profile matrix and explicit row inputs.
