## Context
Actual launcher runs inside bash/uv, children use separate sessions; direct Python TERM worked while parent-tree termination previously left temp files. Signal target matters.
## Goals / Non-Goals
Goal: SIGHUP enters existing terminate→finally→stop path and finishes owned cleanup.
Non-goals: supervisor/dependencies, SIGKILL guarantee, remote operations, new generic signal framework or data contract.
## Decisions
Register SIGHUP with existing terminate; ignore it during stop alongside existing INT/TERM masking so repeated terminal signals cannot interrupt cleanup.
Preserve loopback8000/5173 strictPort, two child groups, unique temp root, existing5sec grace/kill/wait.
Test README command itself with actual terminal closure (PTY/tmux disposable isolated pane); direct Python signal is additional evidence, never substitute for closing real terminal.
If wrapper hangup prevents cleanup despite handler, diagnose exact signal path before minimal script-only change; no workaround that simply stops the test server externally.
## Invariants and siblings
Only recorded owned child groups and created temp root are cleaned; unrelated listeners/files survive.
Siblings are shell/uv wrapper, embedded Python handler/finally, child sessions and tmp path. No duplicate production launcher.
## Sketch seams under test
Extract unique README sh block unchanged, start actual services, capture root/PIDs. First close an exclusive actual test terminal/pane; separately restart and deliver SIGHUP directly to the verified Python launcher plus repeated SIGHUP during cleanup. BOTH seams require bounded wait for both groups/ports/root absent. Neither substitutes for the other. Old command leaks, new passes.
Preserve CtrlC, direct TERM, child exit and occupied fixed-port failure; no mocks in runtime proof.
## Risks / Trade-offs
Terminal signal routing differs by wrapper/PTY; report exact exercised seam. Linux/macOS both supported by POSIX API; local platform proof not universal shell guarantee.
## Not yet specified
None; script-only handler and truthful lifecycle proof.
