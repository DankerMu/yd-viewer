# raw-staging-error-vocabulary Specification

## Purpose
TBD - created by archiving change pin-raw-staging-error-kinds. Update Purpose after archive.
## Requirements
### Requirement: Preserve the closed raw staging error vocabulary
ERROR_KINDS MUST equal frozenset({"incomplete-verdict", "unsupported-layout", "source-symlink", "source-manifest", "verdict-mismatch", "accumulation-metadata", "source-mutated", "target-exists", "copy-failed"}). A regression test MUST assert exact equality against these independent literals.
#### Scenario: Vocabulary remains exact
- **WHEN** the unchanged error vocabulary is imported
- **THEN** the exact-set regression passes
#### Scenario: Vocabulary silently drifts
- **WHEN** a tenth item is added or one literal is renamed
- **THEN** the exact-set regression fails

