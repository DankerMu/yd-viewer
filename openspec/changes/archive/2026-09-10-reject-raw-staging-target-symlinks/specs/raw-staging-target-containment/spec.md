## ADDED Requirements

### Requirement: Reject symlink descendants before raw staging writes
stage_raw MUST reject any pre-existing symlink component below work_dir along a bundle or manifest destination with RawStagingError using the existing ERROR_KINDS vocabulary. Refusal MUST add or remove no paths in work or the symlink target. It MUST NOT resolve and rewrite the destination. work_dir itself MAY be a symlink to a valid independent directory. Existing source guards, no-clobber, claim ownership and manifest contracts MUST remain unchanged.

#### Scenario: External intermediate link
- **WHEN** raw, source, or cycle destination directory below work is an external symlink
- **THEN** staging raises RawStagingError before any write and recursive work and target snapshots are unchanged

#### Scenario: Internal dangling or leaf link
- **WHEN** a destination component is an internal link, dangling link, or bundle/manifest leaf link
- **THEN** staging rejects without writing into or changing work or link targets

#### Scenario: Root alias compatibility
- **WHEN** work_dir itself is a symlink to an independent directory and its destination descendants contain no links
- **THEN** staging succeeds with byte-identical copies and valid manifest physically under that directory

#### Scenario: Existing ordinary destination
- **WHEN** a destination leaf is an existing regular file
- **THEN** staging preserves the file and raises target-exists
