## Why
Starlette TestClient warns on old httpx and AnyIO4.15's BlockingPortal alias. User approved httpx2 with temporary AnyIO<4.15, accepting shared runtime lock downgrade4.15.1→4.14.2.
## What Changes
Replace devhttpx with httpx2 and explicit dev compatibility upper bound on AnyIO; freeze minimal changed resolution only. No filters or API implementation edits.
## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `viewer-config-health`: reproducible warning-free HTTP test-client compatibility.
## Impact
viewer pyproject/uv.lock, only necessary existing test adaptation if required. Shared lock affects production AnyIO version by approved decision; runtime dependency declarations unchanged.
Issue type: test/release
Fixture level: expanded
Upstream suggested level: absent; dependency compatibility/shared frozen runtime graph triggers expanded.
Selected risks: dependency/config, HTTP error/health preservation, docs, Python/platform packaging.
Evidence floor: isolated Python3.12 actual TestClient candidate zero warnings; clean frozen3.12 and current dev interpreter fullsuite zero2named warnings; realuvicorn health200/503, no warning suppression.
