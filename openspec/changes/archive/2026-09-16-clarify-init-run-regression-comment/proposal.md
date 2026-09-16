## Why
Issue #228: the init→run regression still calls run “staged/unimplemented”, although it enters the production path. This is stale test commentary, not a runtime defect.

## Triage
Issue type: test
Fixture level: none
Upstream suggested level: absent (none: two comment lines only)
Blast radius: maintenance explanation only; no executable changes
Selected risk packs: Documentation / migration notes
Evidence floor: inspect comment-only diff; existing init→run test, producer lint/format, OpenSpec/log checks and CI
Design omitted at none level.

## What Changes
- Explain states guard passage and the synthetic fixture's EXIT_RUNTIME=3 outcome without implying an unimplemented run stub.
- Keep `cli.main(run_argv, env={}) == 3` and stderr absence of “状态目录” assertions unchanged.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `init-bootstrap`: clarify regression documentation only, no runtime contract change.

## Impact
Only producer/tests/test_init_write_phase.py comment plus required workflow artifacts. No CLI, guard, production wiring, dependency or fixture expansion.
