## Context
Issue #99; yd-viewer profile. Expanded fixture, high repair intensity (producer/consumer metadata identity). Upstream suggested level absent. Minimal slice: two carried values checked in _carried_metadata next to time checks.
## Goals / Non-Goals
Require filter Mapping, explicit shortName key, and equality with carried grib_short_name. Mismatch diagnostic includes lead, variable and both actual values. Keep six original values and unrelated filter keys unchanged when consistent. No alias table, canonicalization, overwrite of one side, new string-domain restrictions, converter change, other metadata relations, source-loader or malformed idx-selector policy, #89/macOS CI or #100 split.
## Decisions
Use one local shape-and-equality guard, not a relation table or serializer normalization. Missing shortName is invalid even when grib_short_name is None; distinguish key absence from an equal null. No additional value-domain policy beyond this issue's equality contract. Existing source-manifest kind and admission floor remain owner. Source fixtures that intentionally test later UTF-8 serialization must make both short names the same surrogate so the earlier guard does not mask that oracle; retain existing assertions.
## Invariant Matrix
Governing invariant: each accepted output entry preserves two equal source GRIB identities; contradictory/undefined filters never reach writes.
Source of truth: cfgrib_filter_by_keys.shortName and grib_short_name of the same source entry, not variable aliases.
- Producer/entrypoint: stage_raw -> _build_entries -> _carried_metadata.
- Validators: new local consistency guard beside unchanged time checks; shape precedes value access.
- Storage/failure: serialization, copy/manifest writers and claim release unchanged; failure remains admission source-manifest.
- Consumers: converter filter priority and LocalObjectStore unchanged; no GRIB execution claimed.
- Evidence: public stage_raw snapshots, mismatch diagnostic actual values, verbatim positive metadata, existing serializer/claim regressions.
Rows: apcp filters changed to 2t-WRONG -> source-manifest with lead/variable/both values and unchanged raw/work; opposite-side mismatch -> same; non-Mapping filter -> typed error; missing shortName even with None peer -> typed error; equal custom name plus extra filters -> source metadata retained verbatim; equal surrogate names -> existing UTF-8 serializer rejection remains exercised; #75 nonfinite filter values still rejected.
Boundary checklist: all requested lead/variable entries, shape/missing/equality, pre-copy errors, unchanged time/accumulation/serialization guards, claim release and downstream priority.
## Risks / Trade-offs
No new pin claim: source values are checked against each other only. New guard can precede a later error on multiply-invalid input; setup-only alignment preserves later-error regression reachability. Source manifest input stays unchanged.
