## ADDED Requirements

### Requirement: Fixed complete yd prepared variant

The producer MUST support the current single `yd` model using this fixed native set: `yd.cfg.ic`, `yd.cfg.para`, `yd.cfg.calib`, `yd.sp.mesh`, `yd.sp.att`, `yd.sp.riv`, `yd.sp.rivseg`, `yd.para.lc`, `yd.para.soil`, `yd.para.geol`, `yd.tsd.lai`, `yd.tsd.mf`. A prepared source variant MUST contain these twelve files, opaque `yd.binding`, and `yd.direct-grid-handoff.json`. The handoff MUST use `yd.prepare.direct-grid-handoff.v2`, preserve its existing identity/direct-grid fields, fix the sp.att name to `yd.sp.att`, and use one `file_checksums` map for the thirteen non-manifest files with existing SHA-256 syntax. Binding/sp.att references in the existing direct-grid contract MUST agree with those file checksums. This MUST NOT become a configurable asset-role/target-path framework.

#### Scenario: Complete source variant is consumed
- **WHEN** a GFS or IFS variant contains the fixed files and a matching v2 handoff
- **THEN** the existing prepared loader returns that source's native inputs and direct-grid binding through one contract, using existing read/size/no-follow conventions rather than a new validation stack.

#### Scenario: Five-only variant requires regeneration
- **WHEN** a production run is given a legacy v1/five-only variant or a required native file is missing
- **THEN** it fails before worker submission with a regenerate/prepare or missing-file diagnostic, without inventing assets or upgrading the input silently.

### Requirement: Reuse existing staged work ownership

The producer MUST extend the existing staged file transfer and checksum map to the fixed native set and cycle state, using schema `yd.run.staged-inputs.v2`. Existing work claim, local read boundaries, failure retention and cleanup ownership MUST remain unchanged. Worker input MUST remain entirely work-local. No new snapshot ID, registry, second package hash, arbitrary path mapping, or helper-by-helper whole-package rescan SHALL be introduced.

#### Scenario: Source disappears after staging
- **WHEN** the complete variant and cycle state have been staged into the claimed work and the source root becomes unavailable
- **THEN** native assembly uses only that work-local input and succeeds without accessing the original NFS path.

#### Scenario: Existing retained work remains retained
- **WHEN** an existing timeout/crash policy retains exact work
- **THEN** the expanded staged inputs remain part of that work and are neither separately cleaned nor copied to an external cache.

### Requirement: Stock SHUD native materialization

`assemble_staged` MUST place native files under `<work>/model/input/yd/`, set `RunDirectory.path` to `<work>/model`, and return actual nested state/parameter/index paths without adding public fields. It MUST replace the baseline initial state with the current source's T state, render `yd.cfg.para` using existing runtime parameter rules, and install current forcing as `yd.tsd.forc`. CSV files MUST remain at their existing flat relative names under `model/`; the native forcing index path line MUST be `.` so stock SHUD resolves the same files relative to its cwd. Assembly MUST NOT alter forcing values, station identities/order, coordinates or Z while relocating the index.

Native parameter output MUST use the stock reader's `KEY<whitespace>numeric-value` syntax without a leading space, not `KEY = value`. The shared writer MUST recognize the baseline's native whitespace syntax and case-insensitive runtime keys, writing exactly one native-readable occurrence of each of the six existing runtime parameters. Native recovery MUST use the same syntax with END=0.5. Legacy template rendering remains a separate explicit mode of the shared writer; it MUST NOT dictate native serialization.

#### Scenario: Native reader resolves current run inputs
- **WHEN** assembly receives a current T state and a valid forcing package
- **THEN** every fixed native path needed by the supported yd model exists under `input/yd/`, the returned state is T rather than the prepared initial state, and resolving every forcing CSV as the stock reader does from model cwd reaches the actual current CSV bytes.

#### Scenario: Stock parameter parser reads the intended values
- **WHEN** native assembly renders an actual whitespace-separated cfg.para with old END/output interval and then renders a recovery version
- **THEN** the stock `%s %lf` grammar reads exactly one numeric value per runtime key, including END=7/0.5, DT_QR_DOWN=60 and Update_IC_STEP=720; no old duplicate or equals-sign value remains.

#### Scenario: Primary and recovery retain distinct outputs
- **WHEN** the primary or twelve-hour recovery invocation is constructed from the native RunDirectory
- **THEN** cwd is the model root, only stock-supported project/`-o` arguments are used, the real nested parameter file is rendered for that invocation, and recovery's explicit output directory does not replace the primary DAT destination.

### Requirement: Basin-specific scope without weakening existing safeguards

This change MUST limit new native support to the current yd no-lake/no-external-boundary model. Preparation MUST reject nonzero element BC/SS/LAKE or river BC explicitly rather than omit required conditional data. This check belongs at prepare, not repeatedly in each worker helper. Existing state, work-boundary, publication-order and legacy external-root assembly contracts MUST remain in force; no native v1 fallback SHALL be added.

#### Scenario: Unsupported model physics is not silently discarded
- **WHEN** an input requests lake, source/sink or external boundary behavior outside the fixed yd scope
- **THEN** prepare reports unsupported model input before publishing either source variant.

#### Scenario: No false native completion evidence
- **WHEN** local native-path and synthetic worker checks pass
- **THEN** the result is recorded as local structural/adapter proof, not actual SHUD numerical, Slurm or NFS completion; M4 retains its real-site oracle.
