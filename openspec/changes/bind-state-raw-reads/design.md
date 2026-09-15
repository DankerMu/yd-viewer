## Context
Baseline after #110/PR223 and archive PR224. safe_fs already supplies the exact requested nonblocking regular-file open; this issue connects the remaining unsafe readers.
Change surface: controller._read_header_line, state.cfg_ic._read_bytes_limited/parse error boundary, rawscan._is_readable, affected tests.

## Goals / Non-Goals
Governing invariant: bytes come only from the same no-follow opened descriptor whose fstat established regular-file identity; FIFO replacement cannot block an open while the controller holds flock.
Must preserve: header streaming/chunk budget/MAX_HEADER_LINE_BYTES, cfg.ic max_bytes+1 and byte-for-byte render, bytes-like parser inputs, raw expected-file rules and empty regular file readability.
Must preserve: no writes/deletes/retries/watchdogs; existing root configuration ownership; parser public ValueError and controller STATE_UNREADABLE.
Non-goals: no arbitrary NFS/device I/O timeouts, permissions redesign, state-format changes, or repair of independent safe_fs parent-directory close defects tracked in #183.

## Decisions
Use existing open_file_no_follow directly or through its existing bounded read helper; no second low-level opener. safe_fs implementation remains unchanged.
Controller must retain streaming header scanning, not slurp the full IC file. cfg_ic may reuse read_bytes_limited_no_follow; parse wraps SafeFilesystemError alongside existing OS read errors into ValueError. Bytes-like inputs do not use filesystem gates.
Reader ownership begins when the existing primitive returns its fd; early return, read failure and cancellation must close that returned fd once. Close-only OS failures remain classified at the caller; preserve primary control-flow exceptions rather than accidentally mask them. Prefer existing cleanup conventions over a new exception framework. Internal acquisition failures in the unchanged shared primitive are a separate owner, not permission to expand this patch.
No reader may resolve its input to bypass no-follow. Configured yd_root aliases already work via #110; independent parse/raw/scratch paths must be supplied with physical ancestors. This is the requested compatibility change, not authorization to canonicalize more config fields.
Raw precheck remains: missing/ENOTDIR and preobserved directory/FIFO/broken or directory-target symlink -> missing; access errors -> unreadable. A regular-target symlink or any path whose read-side no-follow/identity/open/read check fails -> unreadable. A prechecked regular file changed into FIFO/symlink/another inode must not become ok. Do not make all refusals missing.
State precheck classifications remain; read-side failures map to STATE_UNREADABLE. cfg.ic path no-follow refusals map to ValueError. Init calibration symlink now fails at parser read with zero state writes, not a change to discovery's counting algorithm.
Inventory registration belongs to the packages/common/state_qc.py row, whose notes route borrowed _read_bytes_limited to cfg_ic; no new snapshot target row is needed.

## Evidence and Review
Sibling surfaces: decide_frontier and lock-wrapped callers, init calibration parse, state_qc users of cfg_ic helper, raw judge, existing safe_fs descriptor/read tests.
Required evidence: real regular-file pre-stat followed by FIFO replacement -> bounded child returns each caller's refusal; baseline blocking open fails child timeout. Also inject replacement between safe_fs pre-stat and os.open so O_NONBLOCK/fstat are actually exercised, not merely a static FIFO precheck.
Required evidence: no-follow symlink leaf/ancestor refusals, swapped different regular inode refusal, acquired FD closure after success/early header return/read failure/cancellation, existing valid header/IC roundtrip/raw empty regular behavior and caps.
Seams under test: actual three reader call paths, public parse/decide_frontier/judge classification where applicable; targeted safe_fs open race uses its real descriptor path.
Review focus: no blocking pathname open remains in the three readers; exception taxonomy; same FD and closure; user-authorized symlink behavior migration without lost content/budget oracles.
