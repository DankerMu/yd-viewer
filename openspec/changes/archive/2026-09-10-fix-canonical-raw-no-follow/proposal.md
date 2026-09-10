## Why
Issue #103: canonical raw decoding bypasses object-store no-follow access and can turn outside-root bytes into products. Wave 0 decision 1 authorizes converter forks, registered before implementation in the snapshot inventory.
## What Changes
- Bind raw decoding to the existing no-follow primitive; never reopen the original object path after validation.
- Preserve real cfgrib and NetCDF fallback semantics and byte-identical valid products.
- Register the fork and correct issue #71's historical consumer fail-closed claim.
## Capabilities
### New Capabilities
- `canonical-raw-read-containment`: no-follow raw decoding through the conversion boundary.
### Modified Capabilities
None.
## Impact
Canonical converter, yd regression tests, inventory and issue #71 evidence; no store API or dependency change.
