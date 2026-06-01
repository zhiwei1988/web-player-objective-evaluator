## Context

`scripts/evaluator.sh` acquires `/var/tmp/evaluator.lock` through fd 9 and relies on kernel `flock` release when the evaluator process exits. The current script then starts contestant code, MediaMTX, ffmpeg, Playwright/Chromium, and helper scripts while fd 9 remains open. Long-lived descendants can inherit that fd, so the lock can remain held after the evaluator parent has completed cleanup and exited.

The lock is still valuable: concurrent operator invocations must be rejected immediately. The issue is ownership, not the existence of the lock.

## Goals / Non-Goals

**Goals:**

- Ensure only the evaluator parent process holds the evaluator lock.
- Ensure a fresh run can start immediately after the evaluator parent exits, even if a descendant process briefly survives cleanup.
- Preserve `75 / EX_TEMPFAIL` behavior for true concurrent evaluator parent invocations.
- Prevent repeated submissions in the same upload directory from hanging on interactive `unzip` replacement prompts.
- Add regression tests that fail on the current fd inheritance behavior.

**Non-Goals:**

- Change scoring, report, or `score.json` schemas.
- Change the contestant runtime contract beyond lifecycle isolation.
- Replace `flock` with a PID-file or queue-based scheduler.
- Broaden cleanup to kill arbitrary contestant side ports outside the current documented scope.

## Decisions

1. Keep `flock` and close the lock fd before spawning children.

   Rationale: `flock` already provides correct kernel-backed mutual exclusion while the evaluator parent is alive. A PID-file design would be more error-prone because stale PID detection, PID reuse, and crash recovery would need new policy. The fix is to prevent fd 9 from crossing process boundaries.

   Alternatives considered:

   - Delete `/var/tmp/evaluator.lock` during cleanup. This does not release locks held by open file descriptors and can make diagnostics worse.
   - Store the evaluator PID in the lock file. This can improve messages but does not solve inherited flock ownership.

2. Treat child launch sites as explicit lock boundaries.

   Rationale: The safest implementation is to close fd 9 in subshells or command wrappers before launching contestant code, MediaMTX, capture/browser processes, analyzer/scorer helpers, and other external commands that can spawn descendants. This makes lock ownership local to `evaluator.sh`.

   Alternatives considered:

   - Close fd 9 globally immediately after acquisition. That would release the lock too early and allow concurrent evaluators.
   - Rely only on contestant process-group cleanup. That misses MediaMTX/ffmpeg/browser descendants and still leaves a race if cleanup is incomplete.

3. Make extraction overwrite non-interactively.

   Rationale: The evaluator extracts into `dirname <submission_zip>`, so repeated runs against the same upload directory can hit existing files. Interactive `unzip` prompts can stall automation and complicate lifecycle cleanup. The evaluator should overwrite extracted files deterministically.

   Alternatives considered:

   - Require operators to clean upload directories manually. That is brittle and not enforceable by the entry script.
   - Extract into a unique staging directory. This would be a larger workspace contract change because the current spec says extraction lives beside the zip.

## Risks / Trade-offs

- Closing fd 9 in too broad a scope could release the lock while the evaluator parent is still running. Mitigation: tests must verify concurrent evaluator invocations are still refused while the parent is active.
- A helper may still inherit fd 9 if a launch site is missed. Mitigation: add targeted tests that create surviving descendants and verify the next lock acquisition succeeds.
- Some shells or tools may not care about closed fd 9, but scripts sourced into the evaluator shell might. Mitigation: close fd 9 only in child execution contexts, not in the evaluator parent.
- Non-interactive overwrite can replace files left from a previous extraction. Mitigation: this matches the evaluator's repeated-run behavior and is preferable to hanging; results remain isolated under `results/<team_id>_<ts>/`.
