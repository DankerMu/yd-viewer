## 1. Comment maintenance
- [x] 1.1 Replace only the stale two-line explanation; preserve both existing assertions and all executable code.
- [x] 1.2 Run the existing init→run regression, producer lint/format and OpenSpec/log checks; inspect scope before archive/PR.

## Risk packs
- Selected: Documentation / migration notes — task 1.1 diff inspection and task 1.2 existing test; no deployment migration.
- Not selected: Public API / CLI / script entry — no executable change.
- Not selected: Config / project setup — unchanged.
- Not selected: File IO / path safety / overwrite — unchanged.
- Not selected: Schema / columns / units / field names — unchanged.
- Not selected: Auth / permissions / secrets — unchanged.
- Not selected: Concurrency / shared state / ordering — unchanged.
- Not selected: Resource limits / large input / discovery — unchanged.
- Not selected: Legacy compatibility / examples — no behavior or fixtures change.
- Not selected: Error handling / rollback / partial outputs — existing exit semantics only explained.
- Not selected: Release / packaging / dependency compatibility — unchanged.
