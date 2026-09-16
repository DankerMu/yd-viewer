## Risk packs
- Selected Public API / CLI / script entry: all regression lanes exercise cli.main; actual executable permission smoke.
- Selected File IO / path safety / overwrite: root/source/open/iteration/type probes and unchanged tree snapshots.
- Selected Auth / permissions / secrets: injected EACCES and real chmod-000 lane (root skip only for real mode case).
- Selected Error handling / rollback / partial outputs: EIO vs EACCES classification, errno/path, zero write/init/controller; no traceback/init advice.
- Selected Legacy compatibility / examples: #95 presence and current production exit semantics remain.
- Selected Documentation / migration notes: new cli-config requirement, independent archive inside same PR; old M2 unchanged.
- Not selected Resource limits / large input / discovery: no capacity changes, fixed-depth/early-stop preserved.
- Not selected Concurrency / shared state / ordering: no synchronization changes or atomic snapshot guarantee.
- Not selected Config / project setup; Schema / columns / units / field names; Release / packaging / dependency compatibility: unchanged.

## 1. Implementation
- [ ] 1.1 Classify root metadata and nested probe OSError in the state guard, preserving #95 admission and existing absence/type lanes.
- [ ] 1.2 Add CLI-seam EACCES/EIO probe regressions and mode-000 refusal with read-only/no-init evidence.

## 2. Verification
- [ ] 2.1 Record baseline-red/fixed-green refusal lanes and actual CLI permission smoke; keep #95 regressions green.
- [ ] 2.2 Run producer uv pytest, ruff check/format check, large-file gate and OpenSpec strict/all validation.
- [ ] 2.3 Complete fixture/code review and synchronize/archive this independent change in the same PR; final CI and merge are subsequent PR gates, not a claim implied by this checkbox.
