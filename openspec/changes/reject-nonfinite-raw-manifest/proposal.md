## Why
Issue #75: permissive json.dumps lets nonfinite carried metadata escape as nonstandard JSON.
## What Changes
- Serialize raw-manifest with allow_nan=False; existing source-manifest error branch rejects before copying.
- Add nonfinite rejection and strict-parser positive coverage; retire the #75 nonfinite Known-limit entry.
## Capabilities
### New Capabilities
- `raw-manifest-json-finiteness`: strict output JSON with typed zero-write rejection.
### Modified Capabilities
None.
## Impact
rawcopy.py, test_rawcopy.py and OpenSpec only. No source-reader policy, selectors policy, schema shape, dependencies or CI change.
