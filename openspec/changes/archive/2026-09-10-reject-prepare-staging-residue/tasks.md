## 1. Implement and defend startup contract
- [x] 1.1 Add scoped top-level prefix guard after root preflight, before target probes or writes, using existing safe_fs conventions.
- [x] 1.2 Mixed directory/file/link-to-dir/link-to-file/dangling/FIFO entries plus bare prefix and non-hyphen suffix: sorted complete exact absolute-path diagnostic, docs/agent-ops.md/manual cleanup, zero builder/scratch/target probe, unchanged trees and link targets.
- [x] 1.3 One descriptor-bound top-level scan; no exists()-then-list split. EACCES/EIO/ESTALE via the chosen safe_fs primitive (SafeFilesystemError or OSError) yields PrepareError, with no target probes or work. Nested-only/non-prefix entries allow injected-builder success; clean default-builder remains exit 3. Plant top-level prefix entries (dir/file/link/FIFO) in otherwise fresh init lanes: init does not reject as STATES_NOT_EMPTY or delete/claim them.
## 2. Verification
- [x] 2.1 Orchestrator runs a batched red proof against pre-change source in isolated scratch, then final focused tests and a real CLI smoke: residual top-level entries -> exit 1 (not 3), sorted absolute paths and docs/agent-ops.md on stderr, no traceback; agents skip all validation per harness delegation policy.
- [x] 2.2 Orchestrator runs producer and viewer pytest, ruff check/format checks, OpenSpec strict/all validation and stage-log anchor; preserve existing tests and cleanup semantics.
- [x] 2.3 Four high-risk review seats plus final independent review check every invariant row before SHA-bound merge.
