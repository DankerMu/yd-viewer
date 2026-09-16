## ADDED Requirements

### Requirement: Init-to-run regression explanation reflects production semantics
The init-to-run regression commentary SHALL describe passage through the states guard and the existing synthetic fixture's EXIT_RUNTIME=3 result, not an unimplemented run stub. This documentation requirement SHALL NOT change runtime behavior or the existing assertions.

#### Scenario: Successful init unblocks the states guard
- **WHEN** the existing regression initializes its synthetic root and invokes run
- **THEN** the comment explains that states guard no longer rejects and the subsequent non-success runtime outcome uses EXIT_RUNTIME=3
- **AND** the existing exit-code and stderr assertions remain unchanged; full production-run success is not claimed
