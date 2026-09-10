# prepare-staging-preflight Specification

## Purpose
TBD - created by archiving change reject-prepare-staging-residue. Update Purpose after archive.
## Requirements
### Requirement: Prepare rejects protected staging residue before work
After verifying its roots, prepare MUST enumerate only YD_ROOT top-level names and refuse every name starting with `_STAGING_PREFIX`, irrespective of entry type or symlink target. It MUST list all and only matching absolute paths in deterministic sorted order, direct manual cleanup to docs/agent-ops.md, and preserve existing entries. Enumeration failures (SafeFilesystemError or OSError from EACCES/EIO/ESTALE) MUST raise PrepareError; scanning MUST be a single no-follow listing, not exists()-then-list. The CLI MUST report exit 1 and the paths/manual cleanup citation on stderr without a traceback. The check MUST precede final-target probes, scratch creation, builder invocation and other effects. Init MUST continue ignoring root-level staging outside its own guarded lanes.

#### Scenario: Mixed residual entries
- **WHEN** directories, regular files, links to files/directories, dangling links or other entries have the prefix, including the exact prefix and a non-hyphen suffix
- **THEN** prepare refuses with every matching absolute path sorted, manual cleanup guidance, zero work and unchanged existing data

#### Scenario: Discovery failure
- **WHEN** the no-follow top-level enumeration fails with permission, I/O or stale-handle error
- **THEN** prepare raises PrepareError and creates no work or outputs

#### Scenario: Scoped prepare discovery
- **WHEN** only nested prefix names or non-prefix names exist and inputs are otherwise valid
- **THEN** prepare succeeds with an injected valid builder; clean-root default-builder behavior remains exit 3

#### Scenario: Init ignores the reserved root-level namespace
- **WHEN** top-level prefix directory/file/link/FIFO entries exist but init state/output lanes are otherwise valid and fresh
- **THEN** init does not reject as STATES_NOT_EMPTY and neither claims nor deletes these entries

