# Retrospective: switch-to-h265-resolution-profiles

> Written: 2026-05-15 (after verify passed)
> Commit range: `ffaf5f7..1aa8d9f`
> Worktree: main checkout (no git worktree used; consistent with project pattern)

---

## 0. Evidence

- **Commit range**: `ffaf5f7..1aa8d9f` (1 commit; opsx artifacts will land in the archive commit)
- **Diff size**: +917 / −650 lines across 33 files (excluding regenerated `reference/{2k,4k}/*.png`, which would have added 1502 binary files)
- **Tasks done**: 59 / 62 (95.2%) — 3 deferred as explicit "跳过" for docker/portable paths the user plans to retire
- **Active hours**: ~6h (single session)
- **Subagent dispatches**: 0 — apply phase was wide-but-shallow refactor done inline (see §4 for justification)
- **New external dependencies**: none (requirements.txt unchanged)
- **Bugs encountered post-merge**: none post-commit, but 3 bugs caught mid-iteration by `scripts/test.sh` runs (see §2)
- **OpenSpec validate state at archive**: PASS (3/3 valid, INFO-only)
- **Test coverage signal**: 44 pytest cases (6 new `test_profiles.py` + 38 existing/updated `test_score_cpu.py` + `test_cpu_sampler.py`); 7/7 fixtures in `scripts/test.sh` PASS

Commit chain (时序):

```
ffaf5f7 [feat] 添加 CPU 性能测试用例                                       (parent)
1aa8d9f [feat] switch evaluator from codec rounds to H.265 resolution profiles   (this change)
```

---

## 1. Wins

- [evidence: `lib/profiles.py` + `tests/test_profiles.py`] Profile abstraction landed cleanly as the single truth source. Every pipeline module (`runner.py`, `analyzer.py`, `scorer.py`, `report.py`) and orchestration script reads from `PROFILES`; no hard-coded `"h264"`/`"h265"`/port literals remained. Future "add 8K" is now a one-entry change, not a refactor.

- [evidence: §0 commit chain shows `tests/test_score_cpu.py` rewritten before `scorer.py`] TDD on the scorer refactor — wrote the new `test_score_cpu.py` (max_score=30, `measured_4k_fps`, `"4k_round_failed"`, etc.) first, then rewrote `scorer.py` until 37/37 tests passed. Caught two off-by-one bugs in `_build_cpu_block` precedence before they hit integration.

- [evidence: `scripts/test.sh` final run: PASS 7/7] End-to-end self-test working: reference 13/30 (2K full + 4K correctness full but fps-gated CPU); anti-cheat fixtures (static_frame, iframe_only, fake_overlay) all hit expected partial/zero bands; failure fixtures (missing_start, never_ready, missing_testid) all populate correct reason strings.

- [evidence: §2 below + ldconfig step in `scripts/build.sh::apply_ldconfig`] Hit-and-recover on setcap × secure-exec issue: when `ffmpeg` child failed to find `libx264.so.165`, diagnosed root cause (kernel strips `LD_LIBRARY_PATH` from setcap'd binaries), fixed forward by adding ldconfig registration that mirrors the Dockerfile pattern. Took ~10 min from symptom to fix.

- [evidence: `CLAUDE.md` 128 → 54 lines; memory `feedback_claudemd_scope.md`] CLAUDE.md slimming caught mid-cycle (user-flagged). Now future variable-fact changes (ports, scoring numbers, URL formats) won't churn the doc.

- [evidence: `scripts/test.sh --only <case>` flag] Added single-fixture iteration mode on the same day the user complained about 20-min suite runtime. Feedback memory `feedback_test_iteration.md` captures the pattern for future slow-suite projects.

## 2. Misses

- 🔴 [blocking | evidence: user-reported failure on first `./scripts/test.sh` run; `scripts/build.sh::apply_mediamtx_cap` original placement inside `build_mediamtx`'s post-build path] **setcap step skipped on already-built binaries**. Original implementation called `apply_mediamtx_cap` only after `go build`. Users with a fresh mediamtx binary from prior cycle hit `build_mediamtx`'s `is_fresh` early-return, skipping setcap entirely. Fixed by also calling `apply_mediamtx_cap` in the up-to-date branch. Should have manually traced the idempotency path before declaring Task 4 done.

- 🟡 [painful | evidence: user-reported `libx264.so.165: cannot open shared object file` mid-test] **LD_LIBRARY_PATH strip under file capabilities not anticipated**. I knew the abstract mechanism (AT_SECURE flag stripping unsafe env vars) but didn't pre-think it for this change. The design.md mentioned setcap but not the runtime-linker consequence. Caught only when ffmpeg child failed. Cost ~30 min of user-facing debug ping-pong.

- 🟡 [painful | evidence: user feedback "跑一次测试也太慢了吧"] **Test elapsed time mis-estimated at 7-10 min vs actual 20 min**. My plan's analyzer cost estimate was wrong — 750 shots × per-shot SSIM/DataMatrix cost dominates capture time. Should have run a single fixture end-to-end before declaring the plan ready.

- 🟡 [painful | evidence: reference fixture pivot mid-Task-10] **reference fixture initially mimicked old `<video>` H.265 path despite CLAUDE.md warning**. Pre-change CLAUDE.md said «The bundled `reference.zip` self-test uses `<video>` only and therefore fails the H.265 round on this host». I rewrote `reference.zip` keeping `<video>` and didn't read that warning carefully. Pivoted to `<img>`-cycler after first `DEMUXER_ERROR_NO_SUPPORTED_STREAMS`. Should have caught at fixture-design time.

- 📌 [nit | evidence: 1st `Write` to `rtsp_server/mediamtx.yml` returned `File has not been read yet`] Tool ergonomic: I forgot the harness's Write-after-Read rule on a file I'd inspected via cat-in-bash but not via the Read tool. Re-Read + Write fixed it. One-off mistake, not a pattern.

- 📌 [nit | evidence: static_frame contestant.log `ModuleNotFoundError: No module named 'lib'`] Test fixture `start.sh` invoked `.venv/bin/python -c "from lib.profiles ..."` from cwd = submission dir, not repo root. Should have caught in mental simulation when writing the script. Fixed with `PYTHONPATH="${REPO_ROOT}"` prefix.

## 3. Plan deviations

| Plan task | What changed | Why |
|-----------|--------------|-----|
| Task 4 (RTSP setcap) | Added `apply_ldconfig` step to build.sh in addition to setcap | Discovered LD_LIBRARY_PATH strip × secure-exec after test.sh failure; ldconfig registration needed for ffmpeg child to find libs |
| Task 6 (orchestration) | `deploy.sh` reduced to streams-only (removed `ensure_rtsp` + `health_check`) | User-driven mid-cycle refactor — observed that MediaMTX is already per-run owned by evaluator.sh, so deploy.sh's RTSP startup was vestigial |
| Task 9 (reference fixture) | Rewrote from `<video>` source to `<img>` reference-frame cycler | Chrome on canonical host can't HEVC-decode via `<video>` (DEMUXER_ERROR_NO_SUPPORTED_STREAMS); pivot to PNG cycling preserves end-to-end pipeline validation without the codec dependency |
| Task 9 (test.sh) | Added `--only <case>` flag | User feedback that full-suite ~20min iteration is too slow; per-fixture mode brings dev cycle to ~80s |
| Task 11 (docs) | CLAUDE.md slimmed 128 → 54 lines instead of just updating | User flagged that CLAUDE.md mirroring implementation details causes churn every change; restructured to conventions + pointers |
| Task 12 (x264 cleanup) | Decided to KEEP x264 submodule | ffmpeg `configure --enable-libx264` is the assertion; removing would break ffmpeg build |
| Task 10.3 / 11.3 / 7.2 | Deferred as "跳过" — not executed | User signaled intent to remove docker mechanism in a follow-up change; validating soon-to-be-deleted code is wasted effort |

## 4. Skill / workflow compliance

| Skill                                            | Used |
|--------------------------------------------------|------|
| superpowers:brainstorming                        | ✓ |
| superpowers:writing-plans                        | ✓ |
| superpowers:using-git-worktrees                  | ✗ |
| superpowers:subagent-driven-development          | ✗ |
| (transitive) superpowers:test-driven-development | ✓ |
| (transitive) superpowers:requesting-code-review  | ✗ |
| superpowers:finishing-a-development-branch       | (pending /opsx:archive) |

### Deliberately Skipped Skills

- **`superpowers:using-git-worktrees`**
  - **What was skipped**: entire skill — apply phase ran in the main checkout, not in a worktree
  - **Why this cycle**: project has a single-developer pattern (see git log: no worktree-pattern commits in last 5 archive cycles). Branching to a worktree would have isolated the implementation but the user explicitly invoked `/opsx:apply` in the main checkout, signaling main-checkout flow is the established pattern for this repo.
  - **How to prevent recurrence**: `scope-judgment rule` — single-developer repos with established main-checkout pattern (where prior archive cycles never used worktree) should be allowed to bypass worktree skill by default; explicit user request would re-enable. Suggest schema add a hint in `using-git-worktrees`'s frontmatter: "skip if user invokes /opsx:apply directly in main checkout AND no concurrent change is in flight."

- **`superpowers:subagent-driven-development`**
  - **What was skipped**: did not dispatch subagents per task; applied all 13 tasks inline in main session
  - **Why this cycle**: the change shape was wide-but-shallow (33 files, but each edit was a small mechanical substitution: `--codec` → `--profile`, port 8554 → 554, path `h264` → `2k`, etc.). Cross-file dependencies (test_score_cpu.py expects exact field names from scorer.py; runner.py CLI mirrors analyzer.py CLI) meant tasks shared a single working set that would have been costly to thread through subagent dispatches. Test execution is also serial (one MediaMTX per port 554), so parallel agents couldn't have helped on the integration side.
  - **How to prevent recurrence**: `skill description tightening` — `subagent-driven-development`'s description should clarify when NOT to dispatch: when tasks share cross-file invariants that need synchronous human-readable consistency checks (e.g., "rename foo→bar across N files where downstream tests assert on the new name"). Current description is heavy on the "use parallel agents" pull without the inverse pull for "use inline for refactors with cross-file invariants."

- **`superpowers:requesting-code-review`**
  - **What was skipped**: did not request code review on the implementation
  - **Why this cycle**: single-developer organizer-internal project; no reviewer assigned. The `verify` artifact + 44 unit tests + 7-fixture integration test substitute for human review in this project's workflow.
  - **How to prevent recurrence**: `one-off — schema boundary case, no prevention possible`. This is a boundary case because the project has no second reviewer; requesting code review against `claude-code-review` agent makes sense in multi-developer projects but is performative in a single-author repo with comprehensive automated test coverage. Suggest schema PR: `requesting-code-review`'s frontmatter could declare "skip when repo has single-author git log AND `tests/` passes" — but that's a thin heuristic; better as a per-cycle judgment.

## 5. Surprises

- **setcap × secure-exec × LD_LIBRARY_PATH**: I knew CAP_NET_BIND_SERVICE lets non-root bind <1024, and I knew secure-exec strips unsafe env vars on `execve`. I did NOT mentally connect them — i.e., that the mediamtx binary getting setcap would then have ffmpeg as a child process unable to load libx264 from a non-system path. The kernel docs explain this clearly under `AT_SECURE` and `LD_LIBRARY_PATH` semantics in `ld.so(8)`; I should have re-read those before promising "no sudo needed after first build."

- **Reference fixture HEVC limitation was already documented** in the pre-change CLAUDE.md (the `<video>` path always failed H.265 on this host). I skimmed past it when writing the new reference fixture, replicating the old `<video>` pattern with profile-renamed mp4s. Cost a full test cycle to discover.

- **20-min full test cycle**: I estimated 7-10 min based on capture × profile × fixture math. The actual dominant cost is analyzer.py per-shot (DataMatrix + SSIM + color check on 750 shots × 2 profiles), which I hadn't profiled before declaring the plan ready. A 30-second one-fixture dry-run before plan finalization would have caught this and let me size `--only` into the plan instead of as a reactive add.

- **deploy.sh's RTSP startup was vestigial all along**: I copied the pre-change deploy.sh pattern of "ensure_streams + ensure_rtsp + health_check" without questioning. User asked "现在还会启动 rtsp server 吗" and made me realize MediaMTX is already per-run owned by evaluator.sh, so deploy.sh's RTSP block was double-work. Good challenge from the user; reminder that copying-forward old patterns deserves scrutiny.

## 6. Promote candidates → long-term learning

- [x] 🟡 **Idempotent helpers must verify post-conditions, not just rebuild-need** → **Promote to memory** (type: feedback)
  > **Why**: `scripts/build.sh::build_mediamtx` early-returned on fresh binary and skipped the setcap step, leaving the binary uncapped on already-built hosts. The bug isn't in setcap — it's in placing setcap inside a "rebuild" branch as if applying capability == part of building. Capability is a side-effect on the artifact, applied separately.
  > **How to apply**: When writing idempotent build/setup steps, separate "build the artifact" from "ensure post-conditions on the artifact." Post-condition checks should run on every invocation, not gated by `is_fresh`. Applies to: setcap, file permissions, symlink installation, registration steps (ldconfig, systemd reload), env var exports.

- [x] 🟡 **For slow test suites (>5min full run), wire single-case mode early — don't wait for user complaint** → **Promote to memory** (type: feedback, already saved as `feedback_test_iteration.md`)
  > **Why**: This cycle had a 20-min full suite. User flagged frustration after the first full-suite re-run during iteration. Cost was ~15 min wall clock + reactive design work to add `--only`.
  > **How to apply**: When a project's test suite has per-case wall clock > 1 min OR full-suite > 5 min, add `--only <case>` / `--filter <name>` mode to the test harness as part of the initial implementation, not as a reactive fix. Same rule for any iterative debug suite (lints, builds, etc).

- [x] 🟡 **setcap → secure-exec → LD_LIBRARY_PATH strip is a non-obvious chain; document it next to setcap** → **Promote to CLAUDE.md** (already done via Conventions section "Source-build rule" paragraph)
  > **Why**: I knew each piece individually but failed to connect them. Future maintainers reading just `build.sh::apply_mediamtx_cap` see "grant CAP_NET_BIND_SERVICE" and may miss the implication for child processes. The Conventions paragraph now states the chain explicitly so a reader who finds `setcap mediamtx` in build.sh knows to also look for `ldconfig` registration.
  > **How to apply**: When introducing a Linux security mechanism (file capabilities, seccomp, namespaces, AppArmor) that has subtle runtime consequences for child processes / inherited environments, document the chain in the project's CLAUDE.md or equivalent — don't just rely on the implementation comment.

- [x] 📌 **CLAUDE.md scope: conventions + pointers only** → **Promote to memory** (already saved as `feedback_claudemd_scope.md`)
  > **Why**: This cycle had to touch CLAUDE.md 4-5 times for mirrored implementation details (port, URL, scores, file names). User flagged the churn. Slimmed mid-cycle from 128 → 54 lines.
  > **How to apply**: Future projects' CLAUDE.md edits should challenge each line with "would this change just because of a port rename or scoring tweak?" If yes, it doesn't belong in CLAUDE.md — replace with a pointer to the source-of-truth file.

- [ ] 🟡 **`subagent-driven-development` skill description should explicitly cover "wide-but-shallow refactor with cross-file invariants" as a skip-justified pattern** → **Promote to schema / skill PR**
  > **Why**: §4 above shows this cycle skipped the skill for a structurally legitimate reason (cross-file invariants make dispatch costly), but the current skill description pulls strongly toward dispatching without an inverse pull for inline-when-coupled. Future cycles in similar shape will face the same judgment call with no schema hint.
  > **How to apply**: Open a PR against `superpowers:subagent-driven-development` frontmatter to add: "Skip when tasks share invariants requiring synchronous cross-file consistency checks (e.g., signature rename across N files with assertions on the new signature in downstream tests)."

- [ ] 📌 **Manually trace idempotency paths before claiming "Task done" on lifecycle scripts** → **One-off observation**
  > **Why**: I wrote `apply_mediamtx_cap` inside `build_mediamtx` and didn't trace what happens when `is_fresh` is true (the early return path). A 30-second mental simulation would have caught the gap.
  > **How to apply**: Already implied by the Promote candidate above ("post-condition checks separate from build branch"); this is just the specific behavior. Don't double-memo.
