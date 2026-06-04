## Context

`runner.py` already writes `<profile>_screenshots/capture_status.json` for each profile at every key capture phase (`runner.py:904 _write_capture_status`), with the shape `{profile, phase, updated_at_epoch, detail}`. Two organizer-facing consumers read it directly from the run directory: `report.py:255 _capture_status_section` (into `report.html`) and `scripts/diagnose_run.py:160 capture_status_section`.

`result_info.py` renders the contestant contract file `result.info`. Today it is a **pure projection of `score.json`**: `render(score, runtime_ms, run_dir, result_code)` builds the `|info|` and `|debug|` blocks from `score` alone, using `run_dir` only to print a path string into `|debug|`. The renderer is invoked as a subprocess by `scripts/_result_info_lifecycle.sh`, which already passes `--run-dir "${RUN_DIR}"`.

The `result.info` format is a frozen contestant contract (CLAUDE.md "Contestant contract is frozen"); its `|info|` field is contestant-visible (the sample documents it as "给用户展示运行信息"). The spec's "Contest Platform Result Info" requirement currently forbids raw internal diagnostics in `|info|`.

## Goals / Non-Goals

**Goals:**
- Surface each profile's terminal capture `phase` + full `detail` in the contestant-visible `|info|` block, always, for both `2K` and `4K`.
- Emit `detail` verbatim with no whitelist/sanitization; pass internal phase labels through unchanged.
- Amend the frozen spec to authorize this and to register the already-shipped `Runtime Metrics:` block, eliminating the existing code↔spec drift.
- Keep the change to a single consuming module plus tests; no new orchestration plumbing.

**Non-Goals:**
- No change to what `runner.py` writes into `capture_status.json`.
- No change to scoring, `score.json` schema, or the `|debug|` block.
- No change to `report.py` / `diagnose_run.py` (they keep their own direct reads).
- No reduction of `detail` — sanitization is explicitly out of scope per the decided design.

## Decisions

**D1 — `result_info.py` reads `capture_status.json` directly from `run_dir` (Option A).**
`render()` (or a helper it calls) globs `<run_dir>/<profile>_screenshots/capture_status.json` for `profile in ("2k", "4k")` and renders a `Capture Status:` block. Chosen over folding the data into `score.json` via `scorer.py` (Option B) because:
- `result_info.py` already receives `--run-dir`; Option A needs zero new plumbing, whereas `scorer.py` has no `run_dir`/screenshots argument and is invoked from three call sites in `scripts/evaluator.sh` (lines 91, 93, 333).
- `report.py` and `diagnose_run.py` already set the precedent of reading `capture_status.json` directly from the run directory.
- The spec already treats `result.info` itself as a legitimate place where content is finalized, so the renderer reading run-dir artifacts is consistent.
- Trade-off: it breaks the "pure projection of `score.json`" invariant in the module docstring. Mitigation: update the docstring to "projection of `score.json` + `capture_status.json` from `run_dir`". `render()` must tolerate `run_dir=None` (existing tests call it without a run dir) and a missing file.

**D2 — Always emit both profiles; success (`phase=completed`) included.**
The block is unconditional, unlike `Execution Feedback:` which only appears on contestant-side failures. One line per profile in fixed `2K`, `4K` order. Rationale: predictable, uniform output the contestant can always rely on; matches the user's decision.

**D3 — `detail` rendered verbatim, no sanitization.**
When `detail` is a non-empty dict, append its JSON to the phase line, e.g. `- 4K: navigation_timeout {"url": "http://...:8080/play?..."}`. Use `json.dumps(detail, ensure_ascii=False, sort_keys=True)` for deterministic, test-stable output (mirrors `report.py:265`). Phase labels (including `forensics_installed`) pass through unchanged. This is the deliberate contract relaxation captured in the spec delta.

**D4 — Missing / unreadable status renders a stable placeholder.**
If `<profile>_screenshots/capture_status.json` is absent or unparesable (profile never started, or `run_dir=None`), render `- <LABEL>: not run`. Rendering never raises. Reuse the existing `_PROFILE_LABELS` map (`{"2k": "2K", "4k": "4K"}`) for line labels.

**D5 — Block placement within `|info|`.**
Append the `Capture Status:` block in `_build_info_lines` after the existing blocks (Breakdown → Runtime Metrics → Decode Path Violations → Rendered Snapshots → Execution Feedback), using the same blank-line-then-heading pattern. Exact ordering relative to neighbours is cosmetic and pinned by tests.

## Risks / Trade-offs

- [Contestant-visible internal leak] `detail` can contain Chromium version, internal URLs, selectors, clip coordinates, and failure reasons → This is the intended, user-approved relaxation; recorded explicitly in `proposal.md` and authorized in the spec delta so it is not a silent contract change.
- ["Pure projection" invariant broken] `result_info.py` now reads a second artifact → Mitigated by D1's precedent (report/diagnose already do this) and a docstring update; `render()` keeps working with `run_dir=None`.
- [Test stability] verbatim JSON detail could be order-dependent → Mitigated by `sort_keys=True`.
- [Existing tests] `tests/test_result_info.py` calls `render(..., run_dir="/r")` with a non-existent dir → must resolve to `not run` lines, so the always-present block must not break those cases (they assert other fields).
