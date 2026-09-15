# canonical-rawcopy-module-boundaries Specification

## Purpose
TBD - created by archiving change split-canonical-rawcopy-modules. Update Purpose after archive.
## Requirements
### Requirement: Canonical and rawcopy module splits preserve behavior and restore size guards
Each original and split module MUST be below1000lines; exactlythe two original source exclusions MUST be removed without changing threshold/otherexcludes. All existing callable/state/constant behavior, importnames, protectedinjections and testoracles MUST remain. Canonical descendants MUST retain precise NWMprovenance; yd rawcopy descendants MUST NOT falselyclaim snapshotorigin.
#### Scenario: Import and polymorphic compatibility
- **WHEN** existing consumers import oldmodules and invoke GFS/IFS converters or stage_raw
- **THEN** signatures/exports/IFSsuperdispatch/rawcopyfloor remain and alloriginal3515nodeIDs survive; only newprovenance parameters may addcases peruserdecision
#### Scenario: Real output and failure preservation
- **WHEN** baseline GFS/IFS rawfixtures convert andstage or existingunsafe/bounded/claim/error fixtures execute
- **THEN** all42baselineartifacthashes and rawimmutability match, and existingrefusal/no-partial-output oracles remainunchanged
#### Scenario: Guard and provenance closure
- **WHEN** everytenold/newmodule isstaged
- **THEN** strict<1000, exacttwoexclusionremoval, fiveprecisecanonicalheaderrows andactualguardacceptance hold withoutfalse rawcopymarkers

