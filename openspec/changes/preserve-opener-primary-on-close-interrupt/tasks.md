## 1. Implementation and evidence
- [x] 1.0 Register #232 problem and repair in inventory row 42 before source changes (published in da3b1f1); preserve NWM header within HEADER_LINE_BUDGET and do not edit pinned tests/test_safe_fs.py.
- [x] 1.1 Add real-fd secondary-interrupt regressions and record pre-fix red evidence.
- [x] 1.2 Isolate opener close interruptions while preserving primary and pending-parent ownership.
- [x] 1.3 Prove regressions green, including file-then-parent order; keep #225 OSError secondary-note/primary-identity controls in test_safe_fs_open_ownership.py and #122 controls in test_safe_fs_reads.py green. Run #183/#185 regressions only as unchanged sibling controls.
- [ ] 1.4 Run producer verification, strict OpenSpec validation, review and CI; archive in PR before final SHA.

## Risk packs
- Selected: File IO / path safety / overwrite — real-fd one-shot/order tests; existing safety and transfer tests.
- Selected: Error handling / rollback / partial outputs — identity and secondary diagnostic tests above.
- Not selected: Public API / CLI / script entry — no signature or CLI change.
- Not selected: Config / project setup — unchanged.
- Not selected: Schema / columns / units / field names — unchanged.
- Not selected: Auth / permissions / secrets — existing no-follow policy unchanged.
- Not selected: Concurrency / shared state / ordering — no concurrent state transition; local cleanup order covered by File IO.
- Not selected: Resource limits / large input / discovery — no new limits or discovery; fd ownership covered by File IO.
- Not selected: Legacy compatibility / examples — shared reader contracts unchanged; existing regression coverage.
- Not selected: Release / packaging / dependency compatibility — unchanged.
- Selected: Documentation / migration notes — inventory registration in task 1.0; no deployment migration.
