## ADDED Requirements

### Requirement: Transfer directory successor ownership before previous close
The four safe_fs walkers _open_parent_dir, _open_directory_no_follow, open_directory_no_follow and _list_directory_no_follow SHALL track a successfully opened successor as current before closing previous. If previous close fails they SHALL clean successor exactly once, never retry previous, and preserve the previous failure as the primary error or existing list-domain error cause; cleanup OSError SHALL be secondary. Existing no-follow, containment and successful return contracts SHALL remain unchanged.

#### Scenario: Previous close reports failure
- **WHEN** a multi-component walk opens successor and then previous close raises OSError, whether previous was consumed or not
- **THEN** successor and root receive cleanup, successor is attempted once, previous is not retried and its error remains primary or the listing error cause

#### Scenario: Secondary cleanup also fails
- **WHEN** previous close fails and successor or root cleanup also reports OSError
- **THEN** the original previous failure remains primary or cause, secondary errors are recorded and no failed close is retried

#### Scenario: Repeated bounded handoff faults
- **WHEN** each walker repeatedly encounters injected previous-close failure over a bounded deep tree
- **THEN** successfully closable successors do not accumulate across calls

#### Scenario: Compatible successful walks
- **WHEN** root-only or multi-component walking succeeds
- **THEN** returned directory fds remain caller-owned, parent tuple labels and list names/limited sentinel semantics remain unchanged and path-safety checks remain active
