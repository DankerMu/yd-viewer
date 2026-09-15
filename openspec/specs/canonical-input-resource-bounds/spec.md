# canonical-input-resource-bounds Specification

## Purpose
TBD - created by archiving change bound-canonical-inputs. Update Purpose after archive.
## Requirements
### Requirement: Canonical input resources are bounded without changing accepted outputs
Canonical JSON references SHALL use the store's 16MiB bounded read. Raw inputs SHALL be stat-checked and streaming-checked against a versioned 512MiB limit before decode, using existing no-follow access. Decoded raw values SHALL remain a flat NumPy array rather than Python float tuples. Accepted product and catalog bytes MUST remain unchanged.
#### Scenario: Exact and oversized JSON references
- **WHEN** valid raw-manifest or existing grid-definition JSON is exactly the manifest limit or exceeds it by one byte
- **THEN** exact size is accepted and over-limit size raises CanonicalConversionError through bounded reads
#### Scenario: Raw limit and post-stat growth
- **WHEN** raw bytes exceed the limit either at stat or during staging
- **THEN** conversion raises CanonicalConversionError naming input size before any decoder invocation and cleans owned staging
#### Scenario: Exact raw size and NumPy representation
- **WHEN** contained raw input is exactly the limit
- **THEN** it decodes successfully with NumPy float64 values retaining expected flattened order after dataset close
#### Scenario: Compatibility across sources and engines
- **WHEN** valid GFS real GRIB, GFS NetCDF fallback or IFS NetCDF fallback input is converted
- **THEN** product bytes and catalog JSON match the original converter and #103 no-follow refusal remains effective

