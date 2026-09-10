## 1. Cardinality contract
- [x] 1.1 Add shared top-level calibrated-state cardinality validation before YD_ROOT staging; fixed yd.cfg.ic/path and handoff exact-five stay unchanged; no import cycle or #87 refactor.
- [x] 1.2 Public run_prepare tests for each source with zero or two regular suffix files: source/directory/count/all candidate paths, no staging/copy/rename, scratch cleared and old root unchanged. Genuine normal yd.cfg.ic succeeds; alternate sole filename still rejected.
- [x] 1.3 Direct shared locator proves top-level-only with nested second candidate; suffix update/directory/FIFO controls; preserve init readable/unreadable calibration symlink behavior and #96 state guard. Discovery errors and builder-replaced symlink root fail closed without external writes/target reads.
## 2. Verification and review
- [x] 2.1 Parent batched pre-change red (missing count diagnostic, not generic rejection) and focused green/public smoke; calibrated mutants existing-name-only, accept-first/nonempty, recursive scan each killed with honest independent guard accounting.
- [x] 2.2 Parent producer/viewer pytest, Ruff/check-format, OpenSpec strict/all, stage anchor and merge-result pass; cold import prepare/init/controller in both orders succeeds. Leaves skip all validation.
- [ ] 2.3 Four high-risk reviewer seats plus final independent review, CI/evidence gate and preauthorized merge.
