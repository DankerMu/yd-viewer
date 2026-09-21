## 1. Terminal cleanup
- [ ] 1.1 Route SIGHUP into existing cleanup, mask repeat hangups while stopping, document supported trigger.
- [ ] 1.2 Prove old/new README terminal hangup redgreen and retained cleanup modes with actual local services.
Suggested fixture level: expanded
Minimal mergeable slice: atomic - executable command and cleanup proof.

## Risk evidence
- Selected script/resource lifecycle: real terminal close + direct launcherSIGHUP→both owned groups gone and temp removed; old README reproduces leak. Direct Python-only test insufficient.
- Selected fileIO/ownership: only printed unique root removed, unrelated sentinel survives; never broad pkill/rm. Parent cleans only test-created leftovers after red reproduction.
- Selected config/security: loopback8000/5173 fixed, no fallback port/new env/dependencies; synthetic only.
- Selected errors: CtrlC/TERM/childexit continue cleanup; occupied-port case still exits without half-stack. Repeated HUP during5sec stop cannot interrupt finally.
- Selected docs: README only, no new script product or deployment claims; spec metadata additional.
- Not selected public data schema/legacy migration/concurrency/discovery: data/API unchanged, no new workers or migration.
- Parent executes runtime proof and strictOpenSpec; project CI preservation gates. No permanent source-text tests.
