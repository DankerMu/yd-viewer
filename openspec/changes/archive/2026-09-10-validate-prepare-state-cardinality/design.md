## Context
Issue type: bugfix. Profile: yd-viewer. Blast radius: medium (producer/consumer invariant).
Fixture level: expanded. Repair intensity: high (file publication/state). Upstream suggested level: absent; mandatory path/state triggers. Minimal slice: #97 cardinality guard only.
Upstream seams: run_prepare with recording builder; init bootstrap/calibration locator; direct locator top-level-only test explicitly required by parent §10.3.
## Goals / Non-Goals
Add: each source's builder result top-level suffix candidates count exactly 1. Zero/multiple -> PrepareError with source, directory, count and all candidate paths, before any YD_ROOT staging/copy/rename; scratch cleaned and old data intact.
Preserve: fixed `yd.cfg.ic` identity and calibrated_state_path callers, exact-five manifest/handoff no-follow/checksum gates, bounded state reading/parse/river/reach, #83 startup and #96 state-only symlink rule, init's calibration FOLLOW compatibility, two-source all-or-nothing.
Non-goals: #87 structural refactor; new state naming or config; accepting arbitrary one-file aliases; scan recursion; relaxing unknown-entry/no-follow rules; root/TOCTOU/resource-policy redesign; production node operations.
## Decisions
Reuse init's existing top-level regular-file locator/predicate as the single implementation, not a copied glob/predicate. Limit any sharing edit to prepare.py/init.py; resolve the existing controller→prepare import graph without introducing import-time cycles. Candidate scan is only cardinality validation: downstream continues using fixed calibrated_state_path and exact-five loader, never the returned arbitrary candidate as a new accepted filename.
Verify variant directory through existing no-follow primitives before extra discovery so a builder-replaced symlink root cannot be traversed; discovery failures translate to PrepareError, preserve init's DISCOVERY_UNREADABLE mapping. Existing strict loader remains the authoritative entry/type/content validator and still revalidates staging. No change to shared safe_fs or handoff schema.
Current zero/extra cases already reject via exact-five loader: report this honestly; red proof must target missing diagnostic/guard placement, not claim previously successful unsafe publication. Predicate mutation evidence must be discriminating even with the independent exact-set gate.
## Risk packs considered
- Public API / CLI / script entry: selected — PrepareError/CLI exit1 unchanged.
- Config / project setup: not selected — unchanged.
- File IO / path safety / overwrite: selected — no-follow root, no staging or commit on invalid count.
- Schema / columns / units / field names: selected — fixed yd.cfg.ic and exact-five unmodified.
- Auth / permissions / secrets: not selected — no auth change; discovery errors covered by IO.
- Concurrency / shared state / ordering: selected — validate both scratch variants before any YD_ROOT write; race redesign excluded.
- Resource limits / large input / discovery: selected — top-level only; existing read caps retained.
- Legacy compatibility / examples: selected — init calibration and prepare consumers unchanged.
- Error handling / rollback / partial outputs: selected — typed detailed error/cleanup/zero publication.
- Release / packaging / dependency compatibility: not selected — no dependencies.
- Documentation / migration notes: selected — existing ruling consumed; affected comments describe fixed name versus candidate count.
- Geospatial / CRS / shapefile sidecars: not selected — unchanged.
- Time series / forcing / temporal boundaries: not selected — unchanged.
- 状态链 / warm-start 定戳一致性: selected — producer admits no ambiguous calibrated-state set; existing init/header behavior retained.
- NWM 快照溯源与 DB-free 隔离: not selected — unchanged.
## Invariant Matrix
Governing invariant: prepare must not stage/publish a source variant with zero or multiple top-level calibrated-state candidates, and validating cardinality never changes the fixed state identity or relaxes handoff safety.
Identity: literal yd.cfg.ic + exact-five prepared handoff; shared init top-level suffix/regular predicate.
Producers: recording builder filesystem-output seam; validators: prepare._validate_variant + shared locator + handoff loader.
Storage/write: scratch candidate scan, no YD_ROOT writes until both sources valid; own-run cleanup unchanged.
Public: run_prepare/PrepareError/CLI; consumer: init calibration locator, controller calibrated_state_path, state parser.
Failure: count/error/root-symlink -> typed refusal and clean scratch; no commit, no follower read.
Evidence rows: each source × 0/2 candidates -> named source/directory/count/all paths + zero staging/rename and cleanup; valid yd.cfg.ic -> unchanged four outputs; locator one top-level plus nested match -> one top-level only; .cfg.ic.update/directory/FIFO not regular candidates; calibration symlink behavior stays init's default FOLLOW but prepare still strict-rejects links by existing no-follow/entry gates; alternate sole name still rejected by fixed-name handoff.
Boundary checklist: locator shared owner/callers, import graph, root no-follow discovery, both-source prepublish order, entry/content gates, init/controller compatibility.
## Risks / Trade-offs
Two independent guards (cardinality and exact-set) intentionally overlap: cardinality gives an explicit init-compatible contract and diagnostic, exact-set preserves strict v1 product identity. Revert requires no migration.
