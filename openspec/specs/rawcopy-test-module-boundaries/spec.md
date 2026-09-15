# rawcopy-test-module-boundaries Specification

## Purpose
TBD - created by archiving change split-rawcopy-regressions. Update Purpose after archive.
## Requirements
### Requirement: Split rawcopy tests without changing their oracle
Rawcopy test modules and shared fixture module MUST each contain fewer than 1000 lines. All existing assertions, function bodies/decorators, fixture values and collected case names/parameter IDs MUST be preserved exactly once. Shared constructors MUST have one definition in rawcopy_fixtures.py. Structural probes MUST still inspect the actual yd_producer.rawcopy module. No production source changes are allowed.
#### Scenario: Test relocation
- **WHEN** tests and helper definitions move to feature modules
- **THEN** assertion source/AST and definition inventories remain equal, and all156 moved cases plus4 claim cases collect and pass with an identical multiset of case names including parameter IDs; module-path prefixes are allowed to change
#### Scenario: Claim helper import cutover
- **WHEN** shared helpers move to rawcopy_fixtures.py
- **THEN** test_rawcopy_claim_admission.py MUST differ only by replacing `from test_rawcopy import CYCLE, build_tree, make_config` with `from rawcopy_fixtures import CYCLE, build_tree, make_config`; its function bodies and assertions MUST remain unchanged, and no test_rawcopy compatibility re-export shim or collected-test-module helper imports may remain
#### Scenario: Probe remains discriminating
- **WHEN** scratch rawcopy source gains an executable statement immediately before stage_raw's admission try, after any docstring and not inside its write-phase try
- **THEN** the moved structural-boundary probe fails, while unmodified source passes

### Requirement: Restore the existing line guard
.large-file-guard.json MUST remove only producer/tests/test_rawcopy.py from exclude; enabled, maxLines=1000, test_prepare.py and all other exclusions MUST remain unchanged.
#### Scenario: Completed split
- **WHEN** all rawcopy split files are below1000 lines
- **THEN** none is exempted by the guard and the old rawcopy exemption is absent

