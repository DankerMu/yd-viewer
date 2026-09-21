## 1. Compatible HTTP test client
- [ ] 1.1 Replace dev client and constrain only required compatibility seam; regenerate minimal lock without unrelated upgrades.
- [ ] 1.2 Verify clean frozen Python3.12/currentdev suites have no two named warnings, no filters; actual runtime health unchanged.
Suggested fixture level: expanded
Minimal mergeable slice: atomic - manifest/lock/client compatibility proof.

## Risk evidence
- Selected release/config: exact candidate FastAPI0.141.1/Starlette1.6.0/httpx2 2.13.0/AnyIO4.14.2, uv frozen3.12 + localdev; lock runtime subset importable and dev client excluded --no-dev. No unconstrained broad upgrade.
- Selected publicHTTP/errors: existing169tests preserve API bodies, nullablepoints, staticroutes/health; actualuvicorn health empty200/latestnull, eligiblecycle200, sameprocess outputremoved503 detail/nointernalpath.
- Selected docs/legacy: concise dev pin rationale and upstream-removal condition; useraccepted runtime AnyIOdowngrade, no hidden graph change.
- Not selected newIO/auth/concurrency/resource/schema: implementation unchanged, no new knobs/threads/cache/errors/models. Runtime behavior proof covers transitive dependency risk.
- Baseline169pass+two warnings already observed; candidate isolatedactualTestClient returns200 withzero capturedwarnings. Parent cleanenv complete pytest with warnings visible/error (never ignored), ruff and strictspec.
- Agent skip formatter/linter/project-wide suite/build; may isolated dependency matrix/small realHTTP probe, parent runs final gates. No permanent dependency-plumbing tests.
