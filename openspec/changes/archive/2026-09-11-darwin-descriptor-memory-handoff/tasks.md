## 1. Contract and implementation
- [x] 1.1 API proof for immutableFD bytes/nativeDataset/NetCDF4DataStore/xarray both NetCDF3/4; publish active m2 contract/provenance before source.
- [x] 1.2 Darwin-only bounded sameFD immutablememory/checksumexactpayload; no hashthenreread, remove Darwinaliasfallback; Linux successful fstat/optionalchecksum/alias unchanged; all owners cleaned.
- [x] 1.3 Delete obsolete helperalias preference/noalias assertions and /dev/fd close assertion; existingtestfiles under1000/no exemptions; never edit 993line round1closure.
- [x] 1.4 Portable Darwin EBADF regression on allhosts via module-local sys=SimpleNamespace(platform="darwin"): alias-only lstat EBADF while fstat admittedFD succeeds; actual values[1.25,2.5,3.75]/sourcegfs; nativeclosed/FD EBADF.
- [x] 1.5 Post-admission replacement regression: hook after real nofollowopen returns FD, replace with differentbytes, SHA256(originalpayload); original actualvalues/attrs and FD EBADF, not pre-open rewrite.
- [x] 1.6 Growth regression: checksum omitted; initial fstat<=maxbytes then sameFD yields>cap; observed-more-than-cap (not knownsize-exceeds); capturedFD EBADF.
- [x] 1.7 Distinct native-created setup-failure regression: real native exists before datastore/xarray construction raises; native.isopen false, capturedFD EBADF, intended setup exception.
- [x] 1.8 Distinct dataset-close-failure regression: real Darwin native-backed close raises; native.isopen false, capturedFD EBADF, intended close exception; no #122 causal rewrite.
- [x] 1.9 Distinct context-body-failure regression: reader yields, body raises; native.isopen false, capturedFD EBADF, intended body exception when cleanup succeeds.
- [x] 1.10 Force module-local linux for audit alias-unavailable and any retained Linuxclose tests; preserve ForcingProductionError/no-ready. All Darwin regressiontasks1.4-1.9 force localdarwin on every host, never skipif Darwin or shared sys.platform mutation.
## 2. Evidence and delivery
- [x] 2.1 Parent oldreader red/fixedgreen deterministic alias regression; directactualreader smoke values/attrs/FD lifecycle.
- [x] 2.2 Existing canonical admission/checksum/size/caller suites and full serial producer/viewer/ruff/OpenSpec/stage matrix green; no weakened rejection/numeric oracle.
- [x] 2.3 Approved high fixture/invariantmatrix; four-seat review, independentfinal, frozenSHA/CI/evidence, preauthorizedmerge/archive/log.
