## Context
Issue #100, profile yd-viewer. Fixture expanded, repair medium: multi-file test import/collection compatibility and structural probe preservation, no runtime behavior or production IO changes. Upstream suggested level absent. All prerequisite fixes merged: #71 PR199, #52 PR133, #75 PR200, #99 PR203, #76 PR204.
## Goals / Non-Goals
Pure moves only: every assertion source/AST, full function/class AST and module-level data declaration from original test_rawcopy.py retained exactly once. Test node module paths may change, names/parameter IDs and counts must not. Existing claim test bodies/assertions unchanged; only shared fixture import changes. No production source edit, new tests, assertion cleanup, guard threshold change, new exclusions, test_prepare.py exemption removal, macOS CI or stale historical source-comment cleanup.
## Decisions
Shared module rawcopy_fixtures.py holds original common fixture block/constants/helper definitions as one authority, using existing tests/*_fixtures.py convention. Group by existing Row themes: manifest outputs, source gates/metadata, paths/containment, copy/rollback, admission structural probes. Keep probe helpers/ADMISSION declarations together and preserve direct ast/inspect and rawcopy_module imports and target. No compatibility re-export module and no imports between collected test modules. Migrate test_rawcopy_claim_admission.py helper import to rawcopy_fixtures. Retain or remove original test_rawcopy.py according to clean theme layout; any retained file must contain actual tests, not a shim.
## Preservation matrix
- Shared fixture definitions/constants -> exactly one definition/value; importing helpers cannot collect tests.
- Original 144 top-level functions/classes -> same AST including decorators and bodies, exactly once across split files.
- Original 292 assertions -> identical AST multiset and assertion source-text multiset, including helper/nested assertions.
- Collection -> exactly 156 moved cases plus 4 unchanged claim cases (160 total), same name/parameter-ID multiset; no lost/duplicate tests.
- Structural probes -> same rawcopy module import/inspect target; scratch mutation inserts an executable statement before stage_raw admission try, moved structural probe must fail.
- Guard -> all split products below 1000 lines; exclude removes only producer/tests/test_rawcopy.py, enabled/maxLines/test_prepare and other excludes unchanged.
- Runtime -> producer/src and viewer/src unchanged.
## Risks / Trade-offs
Global bindings can break after moves; explicit imports and complete function/assignment/collection inventory plus full suite cover them. Original source/provenance comments referencing historical test_rawcopy names remain untouched because issue100 forbids source edits; current split map lives in this fixture. No new issue for historical references: no runtime effect and explicitly out of scope. No weakening old tests to pass.
