## Risk packs
- Selected File IO / path safety / overwrite: real root-aware append and receipt/collect equality; no alternative fd writer.
- Selected Concurrency / shared state / ordering: sequential stdout/stderr and primary/recovery bytes; primary tracker vs recovery ownership.
- Selected Resource limits / large input / discovery: bounded append observations on 520000/1040000-byte cases, no RSS threshold.
- Selected Error handling / rollback / partial outputs: nonzero recovery, EOF drain and existing tracker failures/parameter restoration.
- Selected Legacy compatibility / examples: existing independent worker integrity and collect tests remain.
- Selected Documentation / migration notes: independent cli-config delta and in-PR archive, M2 untouched.
- Not selected Public API / CLI / script entry: no new entry or public signature; existing private worker seam exercised.
- Not selected Auth / permissions / secrets: existing containment writer reused; no auth/secret policy changes.
- Not selected Config / project setup; Schema / columns / units / field names; Release / packaging / dependency compatibility: unchanged.

## 1. Implementation
- [ ] 1.1 Route recovery through existing chunk reader, remove communicate, preserve primary tracker and recovery ownership.
- [ ] 1.2 Add real-process bounded recovery/output-order/receipt/collect regressions and nonzero recovery preservation.

## 2. Evidence
- [ ] 2.1 Record baseline bounded-read failure and fixed success; actual worker 520000/1040000-byte/checksum/collect smoke.
- [ ] 2.2 Run producer uv pytest, ruff check/format-check, large-file guard and OpenSpec strict/all.
- [ ] 2.3 Complete code reviews and archive/sync this independent change inside its issue PR; final CI/merge tracked in PR evidence afterward.
