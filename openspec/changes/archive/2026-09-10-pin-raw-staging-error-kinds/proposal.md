## Why
Issue #76: no test pins the existing closed nine-kind error vocabulary.
## What Changes
- Add one exact equality assertion against nine literal strings.
- Prove expansion and rename mutations fail; retire the no-discriminator Known-limit.
## Capabilities
### New Capabilities
- `raw-staging-error-vocabulary`: exact existing public error vocabulary evidence.
### Modified Capabilities
None.
## Impact
Test-only, no production/API/schema/error-policy change; no macOS CI; splitting deferred until #100.
