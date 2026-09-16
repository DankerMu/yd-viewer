## Context
Change surface: nwm._run_shud_live and run_private_worker.recovery_runner; corresponding NWM worker tests.
Primary already reads _LOG_CHUNK=65536 with select and drains through EOF after process exit.
Recovery instead calls communicate then a single _append_job_log; historical integrity evidence did not prove bounded consumption.

## Goals / Non-Goals
Governing invariant: job.log equals primary merged stream followed by recovery merged stream, in byte order, with no total-output-sized recovery buffer.
Must preserve: argv/cwd/env, stderr=STDOUT, root-aware _append_job_log safety, return code, primary tracker capture calls and final EOF drain.
Must preserve: recovery remains synchronous/job-local; T+12 recovery validation and parameter restoration remain owned by tracker; collect/receipt checksums unchanged.
Sibling surfaces: primary runner, recovery runner, _append_job_log, ensure_twelve_hour_checkpoint, worker receipt checksum and ProductionAttemptDriver.collect.
Non-goals: fixed memory threshold, truncation/retention policy, timeout/retry, SHUD numerical validation, Slurm/NFS/M4 deployment, new generalized process framework.

## Decisions
Reuse the existing _run_shud_live path rather than copying a second chunk loop.
Recovery must not poll/capture through the primary tracker. A private optional tracker seam may disable only those capture calls; no fake/no-op tracker class.
Keep primary capture-before-read and final-capture behavior unchanged when a tracker is provided.
Recovery passes its existing argv/cwd/work/root/log/env into the shared runner; _append_job_log remains the writer.
Seams under test: actual synthetic SHUD subprocesses through run_private_worker/independent worker and collect; observe sizes at the real append boundary while forwarding real writes.

## Required evidence
Synthetic ordered stdout+stderr > one chunk, with 520000 bytes per SHUD run -> primary-only job.log 520000; primary+recovery job.log 1040000; exact bytes, sha256 and receipt checksum match; collect accepts verified T+12.
Recovery append observation -> multiple payloads each <= _LOG_CHUNK, total recovery bytes exact; never one payload equal to full stream. No RSS/platform threshold.
Trailing output after child exit -> preserved by EOF drain. Original worker integrity tests remain unchanged in meaning.
Recovery nonzero exit -> existing error/checkpoint failure semantics, no successful receipt; primary capture unchanged.
Baseline communicate source -> bounded-recovery regression fails while byte completeness remains; fixed source -> passes.

## Risks / Trade-offs
Reusing tracker-aware loop could capture from wrong directory -> explicitly disable only capture for recovery, preserve recovery owner.
Chunk boundaries vary by OS -> assert maximum and sum/order, never exact chunk count or timing.
## Migration Plan
No data migration. Archive/sync this independent change within the same issue PR before final CI; no M2 task restoration.
