## Context
The user's #110 ruling supersedes the original reject-alias proposal. M2 is already archived; this is a new issue-scoped delta.
Change surface: LocalConfig construction/load_local and every producer local.yd_root consumer.

## Goals / Non-Goals
Governing invariant: one configuration owns one canonical root Path; changing the input alias after construction never redirects any operation.
Must preserve: frozen/keyword-only config; explicit TOML fields; no new defaults; normal realpath roots; unchanged state/publication ordering and no-follow descendant safety.
Must preserve: root existence remains an entrypoint policy, not a new loader requirement; scratch/NWM/lock paths are unchanged.
Must add/change: LocalConfig.yd_root is the sole canonical Path; string/path constructor inputs are accepted and normalized once in __post_init__.
Non-goals: no safe_fs change, root fd lifetime capability, protection against arbitrary mount changes, or normalization of other local paths.

## Decisions
Validate absolute spelling before Path.resolve(strict=False), without expanduser; otherwise relative/~ inputs would become valid accidentally.
Invalid spelling and OSError/RuntimeError from resolution raise ConfigError(path="yd_root"); load_local adds the existing local.toml context.
Non-strict resolve suppresses loop errors on newer supported Python; construction must still classify ELOOP, without requiring an existing directory or introducing a second resolver. The loop regression must never skip this contract.
Constructor normalization covers direct LocalConfig callers as well as load_local; dataclasses.replace creates a new configuration and thus resolves once for that new object.
This moves yd_root absolute-path rejection into configuration construction; #32 other domain ownership is unchanged.
Remove Path(local.yd_root) wrappers and consumer root resolve calls, including prepare and _controller_sources, not only the three originally named files.
Do not call resolve on descendant paths to bypass no-follow safety. Existing safe_fs traversal still refuses substituted canonical-root or descendant symlinks.

## Evidence and Review
Sibling surfaces: CLI states guard, init bootstrap, prepare targets/preflight, controller discovery/startup hygiene/residue/failure cleanup/publish.
Seams under test: load_local to init/bootstrap and CLI run; direct LocalConfig construction; real safe_fs refusal below resolved root.
Required evidence: root alias and ancestor alias reach the same canonical root; retarget alias after load then init writes only original root.
Required evidence: root-internal symlink still refused without outside mutation; relative/~ root gives ConfigError; missing absolute root loads without creation; symlink loop gives ConfigError.
Required evidence: existing realpath and producer tests; actual yd-producer --help and config-error CLI smoke; red baseline for new bug regressions.
Publication additionally proves that retargeting the input alias after configuration cannot redirect DAT/DONE/state, and replacement of the canonical directory by a symlink cannot authorize outside writes.
Review focus: all root consumers migrated; no repeated alias resolution; no weakened descendant protection; error ownership and existing non-root behavior preserved.

## Risks / Trade-offs
Path output changes programmatic representation; migrate all affected callers/tests rather than add aliases. Input alias spelling is not retained as another authority.
Changing the canonical directory itself remains guarded by existing filesystem primitives; resolution is not a long-lived fd capability.
