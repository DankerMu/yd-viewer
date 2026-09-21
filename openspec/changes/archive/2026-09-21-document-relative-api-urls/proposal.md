## Why
Internal URL helper incorrectly appends slash to explicit document bases. Current UI normalizes its base first, but exported helper contract is wrong for index.html/no-extension documents.
## What Changes
Use WHATWG new URL(relativePath,pageUrl) without invented trailing slash or extension heuristic; add focused regression.
## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `viewer-frontend`: document-relative helper semantics.
## Impact
Only frontend lib/api.ts and api.test.ts; UI callers/backend/dependencies unchanged.
Issue type: bugfix
Fixture level: compact
Upstream suggested level: absent; isolated internal pure helper, not a new public HTTP API or shared runtime entrypoint.
Selected risks: URL boundary/legacy directory preservation; no IO/schema/config/security/dependency/concurrency changes.
Evidence floor: explicit-document redgreen plus existing root/prefix/resources, full frontend gates.
Design omitted for compact fixture.
