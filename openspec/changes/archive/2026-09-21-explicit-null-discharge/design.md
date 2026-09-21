## Context
Both actual endpoints currently emit200/null via Pydantic; source selection is unchanged. This does not prove an explicit reader contract. User chose per-point null and retaining source, not reject/fallback.
## Goals / Non-Goals
Goal: one canonical explicit nullable conversion used by row/column; accurate API and TS types; visible gaps.
Non-goals: producer changes, new cache/config/deps, serializer settings, source rejection, zero-fill/interpolation, new DOM test infrastructure, numeric-baseline/M5 claims.
## Decisions
row/column convert finite values /86400, nonfinite discharge to None, only requested slice evaluated; no additional fullfile copy/scan.
Keep minutes strict: nonfinite or off-grid minute remains DatError and existing fallback/omission.
Map nullable annotation must not stringify numbers/null through an overly broad union. Curve response schema must truthfully describe nullable series; use a simple existing-style typed shape, not a new error model/framework.
TS types accept null. Chart passes null points to ECharts with connectNulls false; tooltip must not coerce null to0. Map existing missing color preserved.
Even all-null source stays present and map chooses it by existing priority; no content-based fallback, catalog changes, WARNING or 404 just for discharge null.
## Invariants and siblings
Finite sorting, units, UTC,168lead positions, catalog order and structural/minute failures preserved.
Siblings row,column,map response,curve response,TS api,MapPage,RiverCurveWindow,ForecastChart tooltip/series and SNAPSHOT provenance.
## Sketch seams under test
Reader direct None assertions (HTTP alone already green pre-fix); mixed NaN/+Inf/-Inf + finite values in shuffled columns; actual endpoints retain source even all-null; chart renders gap on real Vite UI.
## Risks / Trade-offs
Missing discharge no longer accidentally depends on serializer. Need explicit schema and avoid null→0 tooltip. Tiny fixtures use visible scaled finite values around missing intervals for browser evidence.
## Migration Plan
Implementer edits docs first and yields; parent commits/pushes docs+fixture before source. Same-PR canonical spec sync/archive after validation.
## Not yet specified
None: user chose policy including gaps/no bridging/no zero fill.
