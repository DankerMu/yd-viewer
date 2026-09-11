## Context and decision
Issue127 root cause is established; do not rediscover the stochastic flake. API feasibility was exercised BEFORE implementation on installed netCDF4 1.7.4/xarray2026.7.0: no-follow FD remains usable after leafunlink, bytes/memoryview -> netCDF4.Dataset(mode="r",memory=...) -> NetCDF4DataStore -> xr.open_dataset(engine="store") reads exact values/attrs for NETCDF3_CLASSIC and NETCDF4 and closes nativeDataset. Evidence .workplans/issue-127/api-{bytes-}probe.txt and runner text. Probe temp root had to use its physical path because Darwin /var is a symlink; production safe_fs is unchanged.

## Risk triage
Fixture expanded, repair intensity high (shared descriptor read/security/resource/cleanup boundary); effective review tier high, four seats correctness,invariant-state,test-evidence+spec-compliance,security-perf+integration. Legacy issue has no upstream suggested tier/minimal slice; one helper boundary plus caller tests is minimal mergeable slice.
Selected packs: API(contract of reader), schema/units(decoded NetCDF compatibility), legacy compatibility(Linux path/default decoding), errors/output(failclosed/close), documentation(active alias contract), geospatial(canonical values/coords), time-series(existing canonical identity/units), NWM/DB-free(registered caller fork, no external DB), streaming/resource guard(512MiB/no-follow/checksum), security(path identity). Auth/UI/distributed state/write/publish/rollback notselected: no changed auth/UI or publication algorithm; existing no-ready consumer failure is regression evidence only.

## Implementation shape
Keep open_file_no_follow(...containment_root=store.root), validation and sameFD fstat first. Select memory branch ONLY on Darwin; other platforms keep Linux alias behavior, including failclosed when /proc alias unavailable. descriptor_alias_path becomes Linux-only; delete Darwin root/fallback, no compatibility shim. Do not change Linux _checksum_descriptor / alias successful xr.open_dataset call or _validate_max_bytes. No barepath or disk-temp fallback, retry, process lock, controller change or common safe_fs refactor.
Darwin uses a small private FD-to-bytes bounded read helper; common safe_fs bounded readers accept paths and reopen, so cannot reuse them for this sameFD capability. Reuse the 1MiB streaming/sentinel guard pattern, with each read <=min(1MiB, remaining+1); reject growth beyond cap BEFORE decoding, including without expected_checksum. Assemble via BytesIO/getvalue to obtain immutable bytes without a chunk-list join copy; hash precisely that immutable payload only when checksum requested, preserving current normalization/error wording. No first hash pass followed by a second unverified read. maxbytes default remains536870912; no new knob.
Open native netCDF4.Dataset("canonical",mode="r",memory=payload), wrap NetCDF4DataStore, then xr.open_dataset(store,engine="store"). Keep payload and FD alive through context. Own native handle from construction onward: on datastore/xarray construction failure, context-body exception or dataset.close exception, close still-open native handle; always os.close(fd) in outermost cleanup. Avoid doubleclosing native already closed by xarray. Preserve existing Linux dataset.close/fd-close exception semantics; do not fold #122 causal-error refactor into this issue.
Use sys.platform branch. Tests forcing platform replace module-local sys binding with SimpleNamespace(platform=...) rather than mutate shared sys.platform and perturb pytest/xarray globals.

## Must preserve / boundaries
Same admitted inode, finite object cap, optional expectedSHA over actual decoder bytes, unchanged caller APIs and CF decoding. Symlink leaf/ancestor, FIFO/directory/device, bad cap, checksum mismatch remain failure. All owners close even after setup/body/close failure. No reopening object path after admission, no /dev/fd operations on Darwin reader. Linux successful alias/xarray call remains untouched. No source edits to producer.py/file_store.py/controller/safe_fs; only helper and tests/docs.

## Invariant Matrix
Governing invariant: every decoded Darwin byte comes from one admitted no-follow FD within cap, matches expected digest when supplied, and cannot outlive unclosed owners; Linux successful read semantics remain unchanged.
Source identity: admitted FD inode + maxbytes + optional expectedSHA; immutable payload is decoder source.
Producers: open_canonical_netcdf memory branch; shared no-follow primitive unchanged.
Validators/preflight: existing cap/type/fstat/checksum gates; streaming growth guard independent of checksum.
Storage/cache/query: no secondpath open/cache; nativeDataset/NetCDF4DataStore/xarray owner chain.
Public entrypoints: canonical grid/field readers and file_store attrs all continue same contextmanager API.
Downstream: decoded coordinates/values/attrs/CF metadata and existing forcing output/ready contracts unchanged.
Failure/stale: knownoversize/growth/checksum/alias/Linux failure/malformed payload/backendsetup/body/close errors close FD and owned native resource; no global lock/retry.
Evidence: parent APIprobe, deterministic oldreaderred/newgreen, direct publicreader smoke, existing canonical rejection suites, full localmatrix/CI.
Regression rows:
- Darwin valid real NetCDF + injected alias lstat EBADF while fstat(fd) succeeds -> exact values/attrs read, no alias-unavailable failure, FD closed aftercontext.
- Darwin post-admission replacement (task1.5/protocol2): real nofollowopen completes before differentbytes replace path; SHA256(originalpayload), actual values[1.25,2.5,3.75]/sourcegfs, FD EBADF afterward.
- Darwin observed growth (task1.6/protocol3): initial fstat<=maxbytes, then sameFD yields>cap, expectedchecksum omitted -> observed-more-than-cap error (not knownsize), FD EBADF.
- Wrongchecksum/knownoversize/invalidcap/unsafeleafancestor/FIFO/directory/device -> existing rejection/no-ready, no leak.
- Darwin setup after native construction (task1.7/protocol4): native exists, datastore/xarray constructor raises -> native.isopen false, FD EBADF, setup error observable.
- Darwin dataset.close failure (task1.8/protocol5): real native-backed dataset.close raises -> native.isopen false, FD EBADF, close error observable.
- Darwin yielded-context body failure (task1.9/protocol6): body raises -> native.isopen false, FD EBADF, body error propagated if cleanup succeeds.
- Linux alias unavailable -> existing ForcingProductionError/no-ready test remains; Linux realkernel alias reader success/decoding remains on Linux CI.

## Scenario evidence and test homes
Use existing test_forcing_helpers_yd.py (449lines) for deterministic realreader aliasEBADF, admitted-inode replacement, growth/no-checksum, backendsetup/body cleanup cases. Reuse existing netcdf_fixture encoder or established real netCDF4 fixture pattern, never new DB/mock backend. Values1.25/2.5/3.75 and source gfs must be asserted from returned dataset, not call arguments. Fault lstat handles /dev/fd and /proc/self/fd only and checks original FD fstat is valid; oldreader fails deterministically, newreader succeeds. Capture real opened FD to assert EBADF after context.
Replace old helpers alias path preference/no-alias unit assertions with consumer-level reader contracts, not new wiring assertions. Existing contract-boundaries close-failure test migrates to real Darwin backend resource cleanup or retains Linux-specific realFD close-failure coverage plus new Darwin counterpart; remove the obsolete /dev/fd text assertion. Existing audit-repair test_produce_rejects_descriptor_alias_unavailable explicitly forces Linux branch, preserving Linux failclosed/no-ready oracle. Other legacy rejection tests unchanged unless genuinely broken contract assertion. Do not add to round1_closure_yd.py (993lines). All edited testfiles remainunder1000/no exemptions.
Parent validates old module overlay against only new Darwin alias regression(s), fixed focused helper/contract/audit/round1/producer_yd/round2 suites, directreader smoke then fullproducer/viewer/ruff/OpenSpec/stage matrix serially. No stale wheel/sourcebinding: fresh uv run python -m pytest or pytest.main after explicit module rebinding; preserve exact runner/logs, remove transient source after proof.

## Risks / rollback / non-goals
Darwin retains at most cap payload plus bounded chunk and native decoding overhead; cap is inputbytes, not total RSS. Memory allocation is intentional to remove unstable pathname. Both NETCDF3/4 actual APIs proved; no scientific-validation claim. Revert this PR to rollback; no persisted schema/migration. Existingpublishedproducts unchanged. No batchG/solver/Slurm/controller/HDF5-lock/close-causality work.

## Boundary-surface checklist
- Shared helper root: netcdf_open only; safe_fs unchanged, admitted FD retained.
- Public/read surfaces: canonical grid/field/attrs consumers; Linuxalias and Darwinmemory branches.
- Write/delete/overwrite: none in production; test path replacement is adversarial input only.
- Staging/publish/rollback: unchanged; existing no-ready failure oracle retained.
- Evidence/readiness: same optionalSHA and cap; dataset/native/FD ownership on all exits.
- Stale/idempotency: no new cache, retry, lock or oldproduct migration.
- Unchanged downstream: registry, controller/publishlock, HDF5 thread strategy and solver/scientific behavior.

## Fixture repair: exact portable regression protocols
All new Darwin regressions MUST run on every host: replace module-local netcdf_open.sys with SimpleNamespace(platform="darwin"), never shared sys.platform or skipif Darwin. Linux alias/no-ready and retained Linuxclose tests force platform="linux". No new Linux observed-growth policy; only Darwin memory read has checksum-independent accumulated-byte guard.
1. EBADF: lstat fault is restricted to /dev/fd and /proc/self/fd aliases; inside fault, fstat(admittedfd) succeeds. Old helper deterministically fails alias-unavailable, new helper yields actual values[1.25,2.5,3.75]/sourcegfs, closes native andFD.
2. Replacement: hook returns only AFTER actual open_file_no_follow admits original inode, then replace path with differentbytes. Supply SHA256(originalpayload), assert returned values[1.25,2.5,3.75]/sourcegfs, notreplacement; capturedFD is EBADF afterward. Never pre-open rewrite.
3. Growth: expected_checksum is OMITTED; initial real fstat returns size<=maxbytes; only AFTER that observation same admitted FD yields >maxbytes (e.g appendonebyte tooriginalinode). Assert `observed more than {max_bytes}` rather than knownsize `size N exceeds`; capturedFD closed. A fixture alreadyoversize at fstat is invalid evidence.
4. Setup cleanup: real native Dataset(memory=payload) succeeds, then NetCDF4DataStore construction OR xr.open_dataset raises. Distinct test, capture nativehandle andrealFD; assert native.isopen false, fstatFD EBADF, intended injected error observable.
5. Dataset-close cleanup: distinct realDarwin native-backed dataset's close raises. Assert native.isopen false andrealFD EBADF plus intendedcloseerror; migrate contract-boundaries existingclose test or add Darwin counterpart. Remove /dev/fd text assertion. No #122 precedence redesign.
6. Body cleanup: distinct successfullyyielded dataset, context body raises; native.isopen false, FD EBADF, bodyerror propagated when cleanup succeeds.
All six are separate behavioral obligations, not alternatives. Keep existing unsafe/checksum/knownsize rejections. No edits to 993line round1 closure test. Delete obsolete helperalias preference/noalias unit tests rather than re-pin wiring.
