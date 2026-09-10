## ADDED Requirements
### Requirement: State symlinks block init independently of targets
Init MUST classify every symlink at states/<source> or any descendant as an existing state entry using lstat, without FOLLOW stat or traversal. It MUST return STATES_NOT_EMPTY in phase A, name the link, preserve entries/targets, and write neither source. Ordinary non-symlink empty directories MUST remain allowed. The explicit state policy MUST NOT change output DONE or calibration discovery semantics.
#### Scenario: All state link targets
- **WHEN** either source lane or a descendant is a symlink to a regular file, directory, missing target, FIFO, inaccessible target, or symlink loop
- **THEN** bootstrap returns STATES_NOT_EMPTY and the link path with written=(), never follows target and leaves both source trees/targets unchanged
#### Scenario: Empty real directories
- **WHEN** the same locations are real empty directories and all other inputs are valid
- **THEN** bootstrap succeeds for both sources with unchanged restamping/write order
#### Scenario: Unchanged sibling consumers
- **WHEN** output contains a symlink named DONE or a symlink directory hiding DONE, or calibration uses a symlink
- **THEN** DONE symlink-to-regular-file returns DONE_PRESENT; dangling DONE and a directory symlink hiding DONE do not block an otherwise valid bootstrap; readable regular-file calibration links still locate, parse and restamp successfully; inaccessible calibration targets still report DISCOVERY_UNREADABLE

#### Scenario: Preserve non-state-policy write failures
- **WHEN** a non-symlink FIFO occupies states/<source>, or an empty-dir-target symlink/FIFO occupies states/ itself, or the final state target is a real empty directory
- **THEN** existing WRITE_FAILED foreign-entry behavior remains unchanged; the state-only symlink policy does not claim these entries as existing states
