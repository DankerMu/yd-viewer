# darwin-canonical-memory-handoff Specification

## Purpose
TBD - created by archiving change darwin-descriptor-memory-handoff. Update Purpose after archive.
## Requirements
### Requirement: Darwin canonical reads are bounded descriptor-memory handoffs
The reader MUST admit one no-follow FD and reject known oversize. Darwin MUST read bounded immutable bytes from that FD, independently enforce observed-byte cap without checksum, and hash the exact immutable object passed to the decoder when expected checksum is supplied. It MUST NOT checksum then reread. Darwin MUST NOT use pathname aliases or barepath fallback. Linux MUST retain fstat, optional streaming checksum and successful /proc/self/fd behavior. Every following scenario is separately REQUIRED; none substitutes for another.
#### Scenario: Portable Darwin alias EBADF
- **WHEN** the Darwin branch is forced on any host using module-local sys=SimpleNamespace(platform="darwin"), alias lstat alone raises EBADF while fstat of the admitted FD succeeds
- **THEN** the real dataset yields values[1.25,2.5,3.75] and source=gfs, native closes and capturedFD becomes EBADF; the old helper fails alias-unavailable
#### Scenario: Post-admission replacement
- **WHEN** a hook replaces the path with different bytes only after real open_file_no_follow admitted the original inode, supplying SHA256(originalpayload)
- **THEN** Darwin returns original values[1.25,2.5,3.75]/source=gfs and closes its FD; pre-open rewrites do not satisfy this scenario
#### Scenario: Observed Darwin growth without checksum
- **WHEN** expected_checksum is omitted, initial fstat observes size<=maxbytes and afterward the same admitted FD yields more than maxbytes
- **THEN** the reader raises the observed-more-than-maxbytes error rather than knownsize-exceeds, does not decode oversize input, and closes capturedFD; admission-oversize alone is not evidence
#### Scenario: Native-created setup failure
- **WHEN** real native Dataset(memory=payload) succeeds and then NetCDF4DataStore or xr.open_dataset construction raises
- **THEN** native.isopen is false, capturedFD fstat is EBADF, and the intended setup exception remains observable
#### Scenario: Dataset close failure
- **WHEN** a successfully created Darwin native-backed dataset.close raises
- **THEN** native.isopen is false, capturedFD fstat is EBADF, and the intended close exception remains observable
#### Scenario: Context body failure
- **WHEN** the reader yielded a real Darwin dataset and the context body raises
- **THEN** native.isopen is false, capturedFD fstat is EBADF, and the body exception propagates when cleanup succeeds
#### Scenario: Existing admission and checksum rejection
- **WHEN** knownsize exceeds cap, checksum mismatches, cap is invalid, or admission encounters symlink/ancestor/FIFO/directory/device
- **THEN** existing stable rejection and consumer no-ready behavior remain without barepath fallback or resource leak
#### Scenario: Linux alias contract
- **WHEN** the module-local platform is linux and alias is unavailable
- **THEN** existing ForcingProductionError/no-ready remains; Linux successful realkernel alias reading and decoded values remain unchanged

