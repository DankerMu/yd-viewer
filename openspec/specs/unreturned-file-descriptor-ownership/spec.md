# unreturned-file-descriptor-ownership Specification

## Purpose
Define file-descriptor ownership from acquisition through successful return, preserving primary failures and one-attempt cleanup in the no-follow opener.
## Requirements
### Requirement: Own acquired file descriptors until successful return
open_file_no_follow SHALL retain ownership of acquired file descriptors through post-open validation and parent cleanup. On any BaseException exit before return it SHALL attempt file close exactly once, preserve the primary exception object, and record cleanup failures only secondarily without retrying or inferring descriptor state.

#### Scenario: Interrupted post-open validation
- **WHEN** fstat of a real acquired file raises a sentinel KeyboardInterrupt or SystemExit
- **THEN** that same object escapes, the file receives one close attempt and the parent receives its normal cleanup attempt

#### Scenario: Cleanup fails alongside validation
- **WHEN** validation fails and file close or parent close also fails
- **THEN** the original validation exception remains primary and cleanup failures are recorded secondarily without retry

#### Scenario: Post-open safety refusal
- **WHEN** fstat raises OSError or post-open regular-file, file identity or parent identity checks reject
- **THEN** the acquired descriptor receives one close attempt and the original error identity or SafeFilesystemError kind is preserved

#### Scenario: Parent cleanup prevents return
- **WHEN** validation succeeds but closing the parent descriptor fails
- **THEN** the unreturned file receives one close attempt and the parent-close failure remains primary

#### Scenario: Successful ownership transfer
- **WHEN** all checks and parent cleanup succeed
- **THEN** the caller receives an open readable fd and is responsible for closing it

#### Scenario: Secondary close interruption during primary cleanup
- **WHEN** post-open validation has failed and closing file or parent raises a non-OSError BaseException such as KeyboardInterrupt
- **THEN** the identical validation primary SHALL escape with secondary role/type/message diagnostics, and each remaining owned descriptor SHALL receive its own close attempt in file-then-parent order, at most once per descriptor
- **AND** neither retry nor inference about failed-close descriptor liveness is permitted

