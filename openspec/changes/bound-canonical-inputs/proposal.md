## Why
Issue #102: canonical manifest/grid JSON reads ignore the existing 16 MiB store limit; raw decoding lacks a size bound and RawRecord expands grids to Python float tuples. Wave 0 already authorizes registered converter forks.
## What Changes
- Use store read_bytes_limited/MAX_OBJECT_MANIFEST_BYTES at both JSON read sites.
- Bound raw input before decoding and while staging; retain #103 no-follow guarantees.
- Keep decoded RawRecord.values as a flat NumPy array without Python-float tuple expansion; preserve output bytes and catalog JSON.
## Capabilities
### New Capabilities
- `canonical-input-resource-bounds`: bounded canonical ingestion with NumPy raw grid retention.
### Modified Capabilities
None.
## Impact
converter.py, yd-owned regression tests, snapshot inventory and issue13 Known limits. No store API, dependencies, numerical policy or forcing changes.
