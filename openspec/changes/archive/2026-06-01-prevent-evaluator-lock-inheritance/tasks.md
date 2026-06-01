## 1. Regression Tests

- [x] 1.1 Add a lifecycle test that acquires the evaluator lock, launches a simulated contestant child that would normally inherit fd 9, exits the parent context, and verifies a fresh lock acquisition succeeds while the child is still alive.
- [x] 1.2 Add a helper-descendant regression test covering a simulated long-lived child launched through an evaluator helper path, verifying it does not retain `/var/tmp/evaluator.lock`.
- [x] 1.3 Add a repeated-extraction test where existing files are present beside the submission zip and extraction completes without an interactive `unzip` prompt.
- [x] 1.4 Verify existing concurrent-run behavior still fails fast with exit `75` while the evaluator parent process is alive.

## 2. Lock Boundary Implementation

- [x] 2.1 Introduce a small shell helper or consistent command pattern that closes the evaluator lock fd in child execution contexts without closing it in the evaluator parent.
- [x] 2.2 Apply the lock-fd close boundary to contestant `start.sh` and `stop.sh` execution.
- [x] 2.3 Apply the lock-fd close boundary to RTSP startup and any MediaMTX/ffmpeg-spawning helper paths.
- [x] 2.4 Apply the lock-fd close boundary to capture, analyzer, scorer, health-check, and result-info helper invocations that can spawn descendants.

## 3. Extraction Behavior

- [x] 3.1 Make submission extraction overwrite existing files non-interactively while preserving single-top-level-directory lifting.
- [x] 3.2 Keep executable permission normalization after overwrite and lifting.

## 4. Verification

- [x] 4.1 Run the focused lifecycle and entrypoint tests added for this change.
- [x] 4.2 Run the existing evaluator shell/static tests that cover single entrypoint, extraction, failure score, and result-info publication.
- [x] 4.3 Manually inspect or script-check that no surviving contestant/helper descendant holds `/var/tmp/evaluator.lock` after a normal run and after a contestant failure run.
