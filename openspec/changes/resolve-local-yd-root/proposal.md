## Why
Issue #110 rejects legitimate deployment aliases and can recommend deleting them. The user explicitly supersedes the issue's rejection proposal with load-time resolution.

Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent (expanded: config and path safety)
Blast radius: prepare/init/run could inspect and write different roots.
Selected risk packs: CLI, config, file IO, compatibility, errors, documentation
Evidence floor: alias-retarget regression, root-internal symlink refusal, producer suite and real CLI smoke

## What Changes
- Store one canonical Path in LocalConfig.yd_root, resolved once per configuration construction; no raw/resolved I/O authorities.
- Accept aliases of the configured absolute root while preserving no-follow checks below it.
- Migrate all consumers; classify invalid or unresolvable root configuration before I/O.
- BREAKING: programmatic yd_root values become Path objects; non-absolute roots fail with ConfigError during construction rather than a later entrypoint guard.

## Capabilities
### Modified Capabilities
- cli-config: canonical configured root and classified construction errors.
- run-controller: ResiduePlan consumes the config-owned canonical root; all identity/zero-delete rules remain unchanged.

## Impact
config, cli, init, prepare, controller run/startup/cleanup/publish callers, related regression tests. No safe_fs weakening, dependency or persisted format change.
