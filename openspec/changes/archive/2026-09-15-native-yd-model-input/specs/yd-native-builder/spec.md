## ADDED Requirements

### Requirement: Real prepare-only NWM library driver

`yd-producer prepare --baseline <model-directory>` MUST invoke a yd-owned thin driver under the exact configured NWM interpreter and produce both configured source variants using NWM mapping libraries. The baseline directory MUST directly contain native yd files and `gis/river.shp`/`gis/domain.shp` with required sidecars. The driver MUST reuse existing grid reader/snapshot preparation, nearest-cell mapping, index assignment, sp.att rewriting, Z-policy sampler and binding emitter; it MUST NOT copy their algorithms into yd. Calling a resolution-only CLI or returning synthetic success MUST NOT count as a build.

#### Scenario: Actual library output becomes prepared variants
- **WHEN** prepare is given a supported complete baseline and the fixed NWM library environment
- **THEN** GFS and IFS each receive their own rewritten sp.att, genuine binding, native files and handoff, and the existing prepare transaction publishes the two variants plus the two GeoJSON outputs.

#### Scenario: Real mapping failure aborts publication
- **WHEN** a reused mapping operation fails because inputs cannot be mapped or parsed
- **THEN** prepare reports the actual builder failure and leaves the existing four-target transaction uncommitted, without replacing the failure with synthetic files or an unrelated reach-count error.

### Requirement: File-only grid inputs without platform governance

The driver MUST use existing NWM checkout grid.json and metadata files for the configured source/grid, materialized through the existing DB-free `read_input_record`/`prepare_snapshot` functions. Any source-to-physical-path mapping MUST be explicit; NWM's `canonical/IFS` input path MUST NOT change yd's existing lowercase `canonical/ifs` daily object key. The adapter MUST NOT connect to DB, register a snapshot, create a new snapshot UUID field or pretend to hold NWM DB registration evidence. It MUST NOT import NWM Approvals, EvidencePackage, CapacityReport or RollbackTarget lifecycle into the yd workflow merely to satisfy the high-level builder API.

The pin-level call/argument recipe in design D4 MUST be followed: explicit grid_definition_uri; a source-normalizing in-memory GridSnapshotLoader retaining snapshot ID None; nearest/used-cell/index with no small-basin override; native sp.att rewrite; real MeshNode elevations through the pinned private mesh helper and Z sampler; and emitter D11 URIs with lowercase yd applicable_source_ids. Transitive imports of platform record types are permitted; constructing those records or executing their lifecycle is not.

The adapter MUST copy BindingArtifact.bytes unchanged and project manifest.to_contract_section_dict() to the existing ten yd contract fields. NWM's bare-hex binding/sp.att checksums MUST be represented with the existing `sha256:` prefix without altering bytes; extra resource_profile/provenance keys MUST NOT be passed into that fixed envelope. Grid signature, station order and numerical values MUST remain unchanged.

#### Scenario: DB-free prepare derives real mapping
- **WHEN** prepare runs with no database credentials using its file grid inputs
- **THEN** real library mapping and binding emission complete without any database connection, registry export or placeholder platform approvals/QA records.

#### Scenario: Emitted NWM binding is accepted by yd
- **WHEN** the real driver emits both source variants through the D4 recipe
- **THEN** the existing yd loader accepts their lowercase source singleton, exact D11 URIs and prefixed checksums, while yd.binding remains byte-identical to the library output and all station Z values come from actual mesh elevations.

#### Scenario: Daily canonical grid differs
- **WHEN** later raw conversion produces a grid incompatible with the prepared binding
- **THEN** the existing forcing compatibility boundary rejects it; neither the worker nor adapter rewrites the canonical grid to force a match.

### Requirement: Existing model identity remains builder-owned

The builder MUST declare the existing model/basin/basin-version/river-network-version values once in the prepared handoff using stable identifiers tied to actual model inputs and mapping version. It MUST NOT copy test literals, mint new per-attempt identity, or introduce a second identity registry. Run/controller MUST continue to consume those declared fields without deriving them from paths or asset contents.

#### Scenario: Same prepared inputs retain identity
- **WHEN** equivalent source-specific baseline/grid/mapping inputs are prepared twice into fresh targets
- **THEN** the existing identity fields and mapping bytes agree, without relying on wall-clock time or random snapshot UUIDs.

### Requirement: Minimal fixed interpreter cutover

The existing mapping-builder invocation boundary MUST execute the packaged yd driver by absolute script path using `local.nwm.python`. It MUST preserve the venv interpreter path rather than resolving its symlink or falling back through PATH. The child MUST use only the explicit NWM checkout as PYTHONPATH, with DATABASE_URL and PYTHONHOME removed; missing interpreter/checkout remains a classified error. The obsolete `nwm_mapping_builder_module` configuration and the builder-unavailable production branch MUST be removed along with their callers. Daily run MUST continue to use yd's environment without importing NWM.

`invoke_mapping_builder` MUST retain its existing name but remove the Config argument used only by the obsolete module field. `run_prepare` MUST bind local configuration explicitly for the real default builder while preserving the injected `Callable[[VariantBuildRequest], None]` seam; the driver MUST NOT discover that context through globals or ambient environment.

#### Scenario: Fixed environment reaches the real driver
- **WHEN** prepare launches from an arbitrary caller cwd with unrelated inherited Python environment entries
- **THEN** it executes the same configured venv interpreter and yd driver, imports the specified NWM checkout, and does not propagate DATABASE_URL/PYTHONHOME or inherited PYTHONPATH entries.

#### Scenario: Missing runtime prerequisite is a real error
- **WHEN** the configured interpreter or checkout is unavailable
- **THEN** prepare fails with the relevant existing configuration error, does not try another interpreter, and does not create published variants.

### Requirement: M2 owns implementation and M4 owns site validation

Native builder/adapter business code MUST be implemented before #132 is considered complete. M4 MUST validate real baseline, configured binary, 00Z/12Z for both sources, seven-day DAT, T+12/next-cycle use and NFS publication; M4 MUST NOT be assigned the missing driver implementation. NWM original files/environment/services MUST remain unmodified by this work.

#### Scenario: Implementation evidence does not replace site evidence
- **WHEN** a local CLI smoke invokes the real pinned mapping libraries on a small valid native model
- **THEN** it proves the adapter execution path only; actual yd node-22 numerical and 22/27 receipts are still recorded separately.
