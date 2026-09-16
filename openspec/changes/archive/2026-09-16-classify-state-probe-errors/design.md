## Context
Change surface: cli._check_states_dir, corresponding CLI state tests.
Current main catches OSError as runtime exit 3; the issue's historical traceback describes old code, not a reason to restore it.
This change returns a classified reason inside the guard so _StatesGuardFailed yields exit 1.
M2 archived Issue #3 is history only; #95 any-source semantics are the new baseline.

## Goals / Non-Goals
Must preserve: any-source file detection; missing/non-directory/empty messages; no init/controller calls on refusal; no writes/deletes/chmod.
Governing invariant: an encountered filesystem probe failure is a guard refusal, never false absence/emptiness or a successful admission.
Sibling surfaces: root metadata, root scandir/iteration, source DirEntry type, source scandir/iteration, state-file DirEntry type and context-manager cleanup.
Non-goals: new symlink policy, retries, all-source prevalidation, permission repair, broad main exception changes, nwm interpreter diagnostics.

## Decisions
Use a raising root metadata probe rather than Path.exists/is_dir predicates that can suppress OSError on supported Python versions.
Preserve missing and non-directory root classifications explicitly; classify encountered PermissionError separately from other OSError.
Return a stable states-probe reason with absolute states path and original errno text; retain failing nested path when available, including an explicit probe path if the exception lacks filename.
Catch only OSError at this boundary, not BaseException or arbitrary programmer errors.
Keep #95 fixed-depth any-source early success: this is not an exhaustive permission audit after a state was found.
Seams under test: cli.main(argv, env); injection only at filesystem probe boundaries, real directory fixtures elsewhere.

## Required evidence
PermissionError(EACCES) and OSError(EIO) at root metadata, root open/iteration, source type/open/iteration, and state type -> exit 1, states path, errno, differentiated permission/IO reason, no init/controller/no traceback/no init advice.
A real mode-000 states directory under non-root -> same permission refusal; explicitly skip the chmod scenario for root, always restore test mode.
Before/after content and mode snapshots -> unchanged; permission fixture retains 000 until test teardown.
Existing #95 missing/file/empty/false-positive and one/two-source lanes -> unchanged.
Baseline source -> new refusal lanes fail; fixed source -> all pass.

## Risks / Trade-offs
Directory iteration can fail after entering a context -> include iteration errors, not only opening scandir.
Missing may arise mid-probe -> classify as IO uncertainty rather than an empty root.
## Migration Plan
No persisted-data migration. User-approved workflow adjustment: archive and sync this issue in its own PR before final CI; merge remains a separate gate after archive.
