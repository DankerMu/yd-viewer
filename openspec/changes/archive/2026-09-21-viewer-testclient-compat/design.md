## Context
LatestFastAPI0.141.1/Starlette1.6.0 and AnyIO4.15.1. Starlette prefers httpx2 but separately references deprecated anyio.abc.BlockingPortal. Isolated candidate with httpx2=2.13.0 and AnyIO=4.14.2 made real TestClient GET succeed with zero warnings onPython3.12.
## Goals / Non-Goals
Goal: remove both specified deprecations with reproducible minimal compatibility set.
Non-goals: warningsfilters, monkeypatch/sitecustomize, alternate HTTP client abstraction, API semantic changes, unrelateddependency upgrades, suppressing producerwarnings.
## Decisions
User explicitly approved shared-lock AnyIO rollback4.15.1→4.14.2. Add dev bound anyio<4.15 with concise rationale/removal condition, devhttpx2 (compatible2.x bound if conventional); leave runtime declaration fastapi/uvicorn unchanged.
uv lock without global upgrade; verify only changed client graph + AnyIO. KeepFastAPI/Starlette/Pydantic/uvicorn unchanged; if resolver needs more changes justify before proceeding.
Do not edit vendored/sitepackages. Existing TestClient tests remain sole seam; no new permanent tests just to verify imports.
## Invariants and siblings
Actual API health emptyoutput200/latestnull, publishedcycle200, deletedoutput503/defaultdetail/nointernalpath; same current nullable map/curve/schema behavior. Sharedlock dev and --no-dev package closure must stay installable onPython3.12.
Siblings pyprojectdev, uv.lock, TestClient import, Docker production uv sync --no-dev, current169backendtests.
## Sketch seams under test
Capture baseline two deprecations (already observed169green+2warnings); isolated candidate actual request zero warnings; clean frozen uv environment all169tests passes without either warning. Actual uvicorn process HTTP health transition200→503 unchanged.
## Risks / Trade-offs
Temporary cap postpones newerAnyIO; remove after Starlette stops deprecatedalias and matrixgreen. Production uses olderAnyIO by userapproval; test actual runtime and --no-dev resolution.
## Not yet specified
None: shared-lock tradeoff explicitly approved in main session.
