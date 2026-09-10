# prepare-state-cardinality Specification

## Purpose
TBD - created by archiving change validate-prepare-state-cardinality. Update Purpose after archive.
## Requirements
### Requirement: Prepare enforces top-level calibrated-state cardinality without changing identity
For each source builder result, prepare MUST validate exactly one top-level `*.cfg.ic` regular-file candidate using the same locating predicate as init, before any YD_ROOT staging or final commit. Zero or multiple candidates MUST raise PrepareError naming source, directory, count and all candidate paths, preserve prior YD_ROOT data and clean scratch. Discovery failures MUST be typed failures, not treated as a successful count. The scan MUST NOT recurse or follow a symlink variant root. VARIANT_CALIBRATED_STATE_NAME MUST remain `yd.cfg.ic`; cardinality MUST NOT weaken the fixed-name exact-five/no-follow handoff contract or change init's calibration visibility.
#### Scenario: Zero or multiple candidates in either source
- **WHEN** a source builder emits zero top-level regular cfg.ic files or yd.cfg.ic plus backup.cfg.ic
- **THEN** prepare reports source/directory/count/all candidates, creates no YD_ROOT staging or final output, and cleans scratch
#### Scenario: Top-level-only discovery
- **WHEN** the shared locator sees one top-level cfg.ic and a nested second cfg.ic, plus cfg.ic.update and nonregular suffix entries
- **THEN** only top-level regular suffix candidates count; the locator finds the single candidate, although prepare may independently reject unknown extra entries
#### Scenario: Fixed identity and compatibility
- **WHEN** an otherwise valid variant contains only yd.cfg.ic
- **THEN** existing successful output remains unchanged; replacing it by an arbitrary alternate-only cfg.ic still cannot satisfy prepare's fixed-name handoff; init's readable and inaccessible calibration-link outcomes remain unchanged
#### Scenario: Discovery errors and unsafe root
- **WHEN** candidate enumeration/stat fails or the builder replaces variant_root with a symlink
- **THEN** prepare fails closed with PrepareError before staging/commit, without traversing the replacement root or mutating existing external data

