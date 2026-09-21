## 1. Document URL resolution
- [x] 1.1 Replace slash-appending with standard document-relative resolution and add failing-before/passing-after boundary cases.
- [x] 1.2 Verify all wrappers and root/prefix/geometry/basemaps paths; frontend preservation gates.
Suggested fixture level: compact
Minimal mergeable slice: atomic - one pure function and targeted regression.

## Risk evidence
- Selected internal API/legacy input semantics: index.html and extensionless document bases→sibling api paths; all cycles/latest/curve wrappers covered. Root and /yd/ directory bases retain prefix; geometry and basemaps relative paths unchanged. Query/hash use standard WHATWG rule, no extension heuristic.
- Preserve caller pageDir normalization and nullable response types; no UI fetch changes.
- Other risk packs not selected: no fileIO/config/schema/permissions/threads/publish/dependencies, same URL constructor errors.
- Redgreen targeted Vitest before/after helper fix; parent actual helper smoke + frozeninstall/typecheck/test/build. No browser claim needed since UI callers unchanged.
