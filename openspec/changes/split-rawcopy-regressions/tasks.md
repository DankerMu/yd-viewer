## 1. Structural split
- [x] 1.1 Extract shared fixtures, preserving definitions/constants; migrate claim-admission helper import only.
- [x] 1.2 Group all original tests/helpers into theme modules below 1000 lines; preserve assertions/bodies/decorators/param IDs and probe imports.
- [x] 1.3 Remove only test_rawcopy.py guard exemption, keep maxLines1000 and all other exclusions.
## 2. Evidence
- [x] 2.1 Compare baseline .workplans/issue-100/baseline-inventory.json:144 definitions,292 assertions, module data declarations and assertion source text unchanged. Compare the Counter of item.name (including parameter IDs) from baseline-collection.json:156 moved+4 claim cases, ignoring module-path prefixes rather than comparing full nodeids/JSON. Diff test_rawcopy_claim_admission.py separately: only its helper import line may change; all its function/assert AST must remain unchanged.
- [x] 2.2 Prove test_admission_phase_is_structurally_enclosed_by_one_floor green, then red when scratch stage_raw gains an executable statement immediately before its admission try (after any docstring), not inside the existing write-phase try. Assert imported rawcopy_module is the scratch module; no permanent runner/source changes.
- [x] 2.3 Run full producer/viewer pytest and Ruff, OpenSpec strict/all, stage log, guard line/exclusion check and final Ubuntu CI.
## Risk packs
- Public API / CLI / script entry: not selected — no product entrypoint change.
- Config / project setup: selected — remove one guard exclusion only.
- File IO / path safety / overwrite: not selected — test relocation only, runtime untouched.
- Schema / columns / units / field names: not selected — no product schema change.
- Auth / permissions / secrets: not selected — untouched.
- Concurrency / shared state / ordering: selected — shared fixture import and probe parameter collection unchanged; no parallel writers.
- Resource limits / large input / discovery: selected — every split product below1000, guard enabled unchanged.
- Legacy compatibility / examples: selected — exact collection/import and full-suite compatibility.
- Error handling / rollback / partial outputs: not selected — no behavior/assertion edits.
- Release / packaging / dependency compatibility: not selected — dependencies unchanged.
- Documentation / migration notes: selected — split map and test_prepare exemption belongs to #82.
- Geospatial / CRS / shapefile sidecars: not selected — untouched.
- Time series / forcing / temporal boundaries: not selected — untouched.
- 状态链 / warm-start 定戳一致性: not selected — untouched.
- NWM 快照溯源与 DB-free 隔离: selected only as preserved test oracle — common constants/provenance and source target unchanged.
Seams: issue100 collection, exact assertion conservation, shared fixture import and rawcopy_module AST probe. No new behavior seam.
