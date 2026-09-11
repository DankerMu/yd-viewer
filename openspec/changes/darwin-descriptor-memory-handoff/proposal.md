## Why
#127 established validFD /dev/fd lstat EBADF on Darwin. Retrying pathname validation cannot protect xarray's subsequent pathname open. Replace Darwin handoff with bounded bytes from the already-open no-follow FD; Linux successful /proc/self/fd path stays unchanged.
## What Changes
Darwin reads one bounded immutable memory payload from that FD, hashes exactly it when requested, and opens netCDF4.Dataset(memory=payload) through xarray.backends.NetCDF4DataStore/engine=store. Preserve bound/no-follow/errors/cleanup. Remove Darwin alias fallback and obsolete alias-only assertions; retain Linux fail-closed behavior and real canonical rejection coverage.
## Capabilities
### New Capabilities
- `darwin-canonical-memory-handoff`: no-alias Darwin reads with bounded descriptor ownership and lifecycle.
### Modified Capabilities
None.
## Impact
Existing netcdf_open.py plus existing helper/contract-boundary/audit-repair tests. Active m2 task contract and snapshot provenance updated before code. No dependencies, new exemptions, production controller/locks, safe_fs, HDF5 policy or #122 error-causality refactor.
