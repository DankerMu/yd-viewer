## Why
Issue #100: after #71/#75/#99/#76 merges, test_rawcopy.py is 3187 lines with 156 collected cases; its large-file exemption hides growth. #52 already merged in PR133.
## What Changes
- Move shared constructors/constants into rawcopy_fixtures.py and tests into theme modules, each below 1000 lines.
- Preserve every assertion, function body/decorator, fixture value and collected case; migrate the claim-admission helper import.
- Remove only test_rawcopy.py from the guard exclusions.
## Capabilities
### New Capabilities
- `rawcopy-test-module-boundaries`: bounded test modules with preserved oracles and active guard.
### Modified Capabilities
None.
## Impact
producer/tests/test_rawcopy.py, new rawcopy_fixtures.py/theme modules, test_rawcopy_claim_admission.py import only, .large-file-guard.json. No producer source or other exemption changes.
