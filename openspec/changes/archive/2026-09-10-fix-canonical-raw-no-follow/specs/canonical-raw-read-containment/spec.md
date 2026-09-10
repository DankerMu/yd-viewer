## ADDED Requirements
### Requirement: Canonical raw reads enforce no-follow containment
Canonical conversion SHALL obtain raw bytes using the existing object-store no-follow primitives with store-root containment. Third-party decoders MUST NOT reopen the original raw object path. Valid input products and catalog semantics MUST remain unchanged.
#### Scenario: Outside symlink in any raw component
- **WHEN** any raw file or raw/source/cycle ancestor is an outside-root symlink before conversion, including a late manifest entry
- **THEN** BOTH GFS and IFS convert_manifest raise CanonicalConversionError with zero writes under canonical/, including products, catalog and grid definition
#### Scenario: Valid real GRIB and NetCDF fallback
- **WHEN** a regular contained raw object is decoded by cfgrib or the existing NetCDF fallback
- **THEN** conversion preserves baseline product bytes and metadata and closes decoder resources and any owned descriptors
#### Scenario: Raw path replaced after safe open
- **WHEN** the original raw pathname is replaced after its no-follow descriptor is obtained
- **THEN** decoding remains bound to the accepted descriptor or privately staged bytes rather than following the replacement
