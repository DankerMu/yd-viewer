## Why
Closing the terminal can bypass launcher cleanup because SIGHUP is unhandled. Issue297 requests the same cleanup path as SIGTERM, not a supervisor.
## What Changes
- Connect SIGHUP to existing terminate/finally cleanup and document it.
- Verify actual README command under real terminal hangup and existing exit modes.
## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `viewer-frontend`: local development launcher terminal cleanup.
## Impact
Only executable viewer/README.md; no runtime package/API/container changes.
Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent; process lifecycle/script entry and deletion triggers expanded.
Blast radius: owned local dev processes/temp files only.
Selected risks: script entry, resource lifecycle, file deletion ownership, local config/security, failure handling.
Evidence floor: actual README SIGHUP red→green, terminal/pane shutdown and CtrlC/TERM/child-exit preservation, no orphan processes/temp.
