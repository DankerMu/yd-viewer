## Context
Issue type: bugfix. Project profile: yd-viewer. Fixture level: expanded; repair intensity: high (path IO). Upstream suggested level: absent. Minimal slice: target-side preflight from #71. #26 closed 2026-09-03; docs/compute-loop-design.md §10 steps 7–8 already require exact fresh WorkClaim.

## Goals / Non-Goals
Reject pre-existing symlinks in every destination-relative component, including leaves, before writes. Preserve root symlink compatibility, source read-only behavior, manifest contents, nine kinds, no-clobber and WorkClaim ownership/cleanup. No new concurrency protocol, adversarial concurrent replacement guarantee for standalone staging, unrelated-link tree scan, controller rewrite, #89/macOS CI, or #100 split.

## Decisions
Mirror existing source-side lstat component walk in admission, before lexists checks and all mkdir/copy/manifest writes. Inspect every target and manifest path below work root; never resolve and rewrite the destination. Missing components end the walk; other access failures fail closed using copy-failed. Symlink rejection uses copy-failed (leaf target-exists remains acceptable only if unchanged no-clobber precedence is explicitly documented). Existing claim-aware descriptor writers remain untouched. safe_fs helpers perform writing or reject root aliases; do not substitute them for this read-only root-exempt preflight.

## Invariant Matrix
Governing invariant: pre-existing target descendant symlinks never redirect stage_raw writes or rollback outside the caller's work root.
Source-of-truth: caller work_dir and relative target components, checked without following descendants.
- Producers/public entrypoints: rawcopy.stage_raw target and raw-manifest paths.
- Validators: target preflight plus unchanged raw/work mutual containment and source symlink checks.
- Storage: _ensure_dir, _copy_one, _write_manifest; no writes before complete target preflight.
- Downstream: controller _controller_run.py stage_raw(..., claim=claim) and LocalObjectStore retain existing contracts.
- Failure/rollback: admission refusal is zero-write; existing _Written/claim release semantics unchanged.
- Evidence: recursive no-follow work/link-target snapshots and existing controller claim tests.
Regression rows: raw/source/cycle/leaf external links, internal links and dangling links -> RawStagingError with unchanged work and target snapshots; valid symlink root -> copied bytes and manifest inside physical root; ordinary destination -> unchanged success; existing ordinary leaf -> target-exists and unchanged bytes; claim-aware caller -> unchanged ownership tests.
Boundary checklist: admission public boundary, all bundle paths and manifest leaf, creation/copy/manifest/rollback writers, unchanged claimed-root identity and downstream object-store reads.

## Risks / Trade-offs
Preflight is not an atomic defense against non-cooperating concurrent path swaps; existing WorkClaim writers retain their stronger guarantees. This issue implements the prescribed static component rejection, not a new standalone race-safe filesystem layer. Root aliases remain explicitly trusted as required by #71.
