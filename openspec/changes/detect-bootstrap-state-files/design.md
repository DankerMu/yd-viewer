## Context
Change surface: cli._check_states_dir and cli.main run delegation.
M2 historical Issue #3/#21 fixtures remain archived and immutable.
The old issue mentions staged-unimplemented exit 3; current production delegation replaces that obsolete branch.

## Goals / Non-Goals
Must preserve: missing/non-directory refusals, guard exit 1, production run exit codes, zero bootstrap/write/delete/chmod.
Must add/change: empty source directories must return the existing authorized-init guidance.
Governing invariant: the guard admits a root only when at least one source directory contains a state file.
Sibling surfaces: controller.visible_state_cycles owns cycle/content validation; init owns state creation; neither changes.
Non-goals: #44 OSError classification, completeness of both sources, parsing/validating cycle or file contents, new symlink policy, recursive discovery.

## Decisions
Inspect only direct source-directory children and their immediate file children using existing STATE_SUFFIX.
A top-level .cfg.ic, unrelated file, suffix-named directory or deeper nested state is not sufficient.
One source with a state suffices; do not require both ifs and gfs.
Preserve existing file-type discovery semantics rather than adding no-follow policy here.
Seams under test: cli.main(argv, env), with production controller and init boundaries recorded.

## Required evidence
Empty ifs directory -> exit 1, authorized-init guidance, absolute states path, no traceback or controller/init calls.
Top-level state/junk/suffix directory/deeper nested state -> same refusal.
One-source and two-source real .cfg.ic files -> locked production delegation remains reachable.
Snapshot path/type/content/mode before and after guard -> unchanged; missing states remains absent.
The existing non-empty-states production test must gain a real .cfg.ic and a docstring explaining the deliberate fixture correction.
New behavior tests must fail against original source and pass with the fix.

## Risks / Trade-offs
Source IO adds an exception surface -> explicitly covered by next issue #44, not silently folded into this PR.
A filename is presence evidence, not validity -> controller remains authoritative.
## Migration Plan
No data migration; deploy the code, rollback by reverting the issue commit.
