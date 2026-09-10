## Context
Issue #75; profile yd-viewer. Fixture expanded; repair intensity high (serialized producer/consumer evidence). Upstream suggested level absent. Minimal slice: explicit allow_nan=False at the existing _render_manifest json.dumps call.
## Goals / Non-Goals
Reject NaN, positive Infinity and negative Infinity within carried fields; finite values remain accepted with unchanged contents, shape, ensure_ascii=False and ordering. Preserve source immutability, zero-write admission, nine kinds, selectors and WorkClaim behavior. User explicitly scopes #75 to serializer: do not change source JSON loader, malformed idx_selectors policy from issue comment, converter, #99 checks or macOS CI. Comment-level selector concern remains unchanged by explicit user scope; no new policy decision or issue in this run.
## Decisions
Use json.dumps(..., allow_nan=False) rather than a recursive custom validator. Its ValueError is caught by the existing source-manifest branch before any copying. Retire only the nonfinite Known-limit statement in m2 tasks; preserve other statements.
## Invariant Matrix
Governing invariant: successfully staged manifests contain only finite JSON numeric values; rejected serialization changes neither raw nor work trees.
Source of truth: carried metadata -> DownloadManifest.as_dict -> strict json.dumps before writers.
- Producer/entrypoint: stage_raw / _render_manifest.
- Validators: unchanged _carried_metadata and _build_entries; existing serialization exception handler.
- Storage/failure: _copy_one/_write_manifest/_Written and WorkClaim release unchanged; no new IO or rollback authority.
- Consumers: LocalObjectStore/converter receive unchanged finite-value schema.
- Evidence: public stage_raw nonfinite cases with recursive snapshots; strict parse positive finite case.
Rows: NaN/+inf/-inf nested in carried cfgrib filter -> RawStagingError(source-manifest), work/raw snapshots unchanged; finite nested numeric value -> strict parser accepts and value preserved; ordinary GFS/IFS and claim admission -> existing suite unchanged.
Boundary checklist: carried nested values, serialization exception typing, admission-before-copy, unchanged source loader and write/rollback consumers.
## Risks / Trade-offs
Source JSON remains permissive intentionally: only values actually carried into output are subject to strict serialization. Existing producer consumer tolerates NaN; this explicit issue tightens output, not parsing upstream evidence. No contradictory existing must-accept rule.
