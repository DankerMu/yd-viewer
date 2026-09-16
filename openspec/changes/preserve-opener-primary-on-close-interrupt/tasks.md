## 1. Implementation and evidence
- [ ] 1.1 Add real-fd secondary-interrupt regressions and record pre-fix red evidence.
- [ ] 1.2 Isolate opener close interruptions while preserving primary and pending-parent ownership.
- [ ] 1.3 Prove regressions green and existing ownership/reader semantics unchanged.
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
- Not selected: Documentation / migration notes — no deployment migration; snapshot deviation registered before code.
