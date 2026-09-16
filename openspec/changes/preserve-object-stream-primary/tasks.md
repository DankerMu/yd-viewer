## 1. Fixture
- [x] 1.1 Register object_store inventory deviation, obtain fixture review pass and strict validate before source edits.
## 2. Repair and verification
- [x] 2.1 Add real-fd stream fault regressions and prove new defect lanes red before fix.
- [x] 2.2 Implement finally close-once with read/close/cancellation precedence; prove stream and checksum consumer behavior green.
- [x] 2.3 Run producer uv pytest, ruff check/format and line guard; OpenSpec strict/all validation.
## 3. Delivery artifacts
- [ ] 3.1 Complete cross-review and same-PR archive/spec sync; merge remains external final SHA gate.
## Risk pack mapping
- Selected Public API / CLI / script entry: ObjectStoreError cause/cancellation identity, chunks and checksum consumer (2.1–2.2).
- Selected File IO / path safety / overwrite: same safe_fs admission, real acquired fd, no-follow/containment regression controls (2.1–2.3).
- Selected Resource limits / large input / discovery: streaming remains bounded by chunk_size, exactly one close attempt (2.1).
- Selected Error handling / rollback / partial outputs: read/close-only/double-failure/KI/SystemExit matrix and consumer no-partial-result (2.1–2.2).
- Selected Documentation / migration notes: own inventory row and archive/main spec (1.1,3.1).
- Not selected Concurrency / shared state / ordering: no asynchronous/concurrent protocol changes; yield lifecycle preserved within Error handling pack.
- Not selected Auth / permissions / secrets: unchanged filesystem admission, no auth or secret surfaces.
- Not selected Legacy compatibility / examples: no caller migration; existing consumers covered in API pack/full suite.
- Not selected Config / project setup, Schema / columns / units / field names, Release / packaging / dependency compatibility: unchanged.
