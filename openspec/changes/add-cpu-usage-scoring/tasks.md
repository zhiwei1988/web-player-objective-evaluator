## 1. Test scaffolds (test-first)

- [x] 1.1 Create `tests/` directory if absent and confirm `pytest` is available in `.venv` (add to `requirements.txt` only if missing, otherwise skip)
- [x] 1.2 Write `tests/test_score_cpu.py` — table-driven cases over `scorer.score_cpu`: full-marks line (5%, 4.99%, 0%), partial-band breakpoints matching the published table (6→10, 7→9, 10→7, 13→5, 15→4, 17→2, 19→1, 20→0), >20% zero, gate `h265_fps_below_threshold` (measured/expected < 0.25), gate `sampler_no_data` (mean=None), explicit `thresholds_used` round-trip check
- [x] 1.3 Write `tests/test_cpu_sampler.py` — spawn `os.setsid()` subprocess that spins one core via `while True: pass`, run `_cpu_sampler.Sampler` for 2 seconds at default Hz against its PGID, assert `mean_percent` falls in `[100/ncpu - 2, 100/ncpu + 2]` (i.e. ~one core normalized to all-cores total), assert `sample_count >= 2`, then kill the helper and assert sampler reports a finite value (no division by zero on cleanup)
- [x] 1.4 Add a focused unit test in `tests/test_cpu_sampler.py` for the vanishing-PID edge case: start sampler, fork a short-lived child under the same session, let it exit mid-window, assert sampler does not raise and returns a non-negative `mean_percent`
- [x] 1.5 Run `.venv/bin/python -m pytest tests/ -x -q` and confirm all new tests fail with NotImplemented / ImportError (i.e. infra wired up, implementation pending)

## 2. Sampler module (`_cpu_sampler.py`, new file)

- [x] 2.1 Create `_cpu_sampler.py` at repo root (sibling of `runner.py`) with `DEFAULT_SAMPLE_HZ: float = 1.0`
- [x] 2.2 Implement a `Sampler` class: constructor takes `pgid: int`, `hz: float = DEFAULT_SAMPLE_HZ`; methods `start()`, `stop() -> SampleResult`. Internally use `threading.Thread(daemon=True)` + `threading.Event` for stop signaling
- [x] 2.3 Implement `_enumerate_pgid_pids(pgid)` reading `/proc/[0-9]*/stat`, parsing field 6 (`session`), returning the set of PIDs whose session == pgid. Handle `FileNotFoundError` / `PermissionError` silently
- [x] 2.4 Implement per-tick jiffies accumulator: maintain `{pid: last_total_jiffies}` dict; on each tick compute `Σ Δjiffies` for PIDs seen this tick; for new PIDs record baseline only (no delta first time); for vanished PIDs do not error
- [x] 2.5 Implement `SampleResult` dataclass with: `mean_percent: float | None`, `sample_count: int`, `sample_window_ms: int`, `ncpu: int`, `clk_tck: int`, `normalization: str = "all_cores_total"`, `pgid: int`, `sample_hz_used: float`. Compute `mean_percent = Σ Δjiffies / (Δwall_seconds · ncpu · CLK_TCK) · 100`; return `mean_percent=None` if `sample_count < 3` or `Δwall_seconds <= 0`
- [x] 2.6 Add module docstring documenting the `setsid` precondition (SID == PGID == CONTESTANT_PID) and the contract with `_contestant_lifecycle.sh::clx_start_contestant`
- [x] 2.7 Re-run `pytest tests/test_cpu_sampler.py -x` and confirm green

## 3. Scorer changes (`scorer.py`)

- [x] 3.1 Add 5 module-level constants at top of `scorer.py`: `CPU_GATE_H265_FPS_RATIO = 0.25`, `CPU_FULL_THRESHOLD_PERCENT = 5.0`, `CPU_PARTIAL_START_PERCENT = 6.0`, `CPU_ZERO_THRESHOLD_PERCENT = 20.0`, `CPU_MIN_SAMPLES = 3` with brief comments noting each is tunable
- [x] 3.2 Implement `score_cpu(mean_cpu_percent: float | None, measured_h265_fps: float, expected_h265_fps: float = EXPECTED_FPS["h265"], gate_fps_ratio: float = CPU_GATE_H265_FPS_RATIO) -> tuple[int, str | None]` per the 5-step ordering in the spec (gate → no_data → full → zero → partial-linear-clamp)
- [x] 3.3 Extend `build_score` signature with optional `h265_cpu_block: dict | None` and `cpu_thresholds: dict | None` (or read both from `h265_metrics["cpu"]`); compute the cpu block including `points`, `mean_percent`, `sample_count`, `sample_window_ms`, `ncpu`, `normalization`, `measured_on_codec="h265"`, `thresholds_used` (6 fields), `gated`, `gate_reason`
- [x] 3.4 Bump `out["max_score"]` from 30 to 40 in `build_score`
- [x] 3.5 Make `out["objective_total"]` include `cpu.points`
- [x] 3.6 Handle the four explicit gate paths: `h265_round_failed` (h265_metrics is None), `h265_fps_below_threshold` (fps_points==0 or computed ratio < threshold), `sampler_no_data` (h265_metrics has no `cpu` field or `cpu` is null or `sample_count < CPU_MIN_SAMPLES`), normal scoring
- [x] 3.7 Extend `_cli()` to recognize a new failure-reason `container_mode_unsupported` and propagate it into `cpu.gate_reason` (the wrapper sets this when running via `evaluator-host.sh`)
- [x] 3.8 Update `scorer.py` module docstring: replace "30-point objective total" with "40-point objective total" and append a one-line note about CPU
- [x] 3.9 Re-run `pytest tests/test_score_cpu.py -x` and confirm green

## 4. Runner integration (`runner.py`)

- [x] 4.1 Add CLI args to `runner.py::_cli`: `--contestant-pgid INT` (optional, default `None`), `--cpu-sample-hz FLOAT` (optional, default `_cpu_sampler.DEFAULT_SAMPLE_HZ`). Mark `--cpu-sample-hz` help string with `(debug-only, will be retired once calibrated)`
- [x] 4.2 Only when `--codec h265` AND `--contestant-pgid` is provided: instantiate `_cpu_sampler.Sampler(pgid=..., hz=...)` before the steady-state capture loop, call `start()`, call `stop()` immediately after the loop exits (even on capture-loop exceptions — use try/finally)
- [x] 4.3 Record `capture_started_at_epoch` and `capture_ended_at_epoch` (use `time.time()`) bracketing the steady-state loop
- [x] 4.4 Write `<screenshots_dir>/capture_meta.json` with the documented shape: top-level `codec`, `capture_started_at_epoch`, `capture_ended_at_epoch`, and nested `cpu` (the `SampleResult` as a dict, or `null` if sampler was not started or returned `mean_percent=None`)
- [x] 4.5 For `--codec h264` or for `h265` without `--contestant-pgid`: do NOT write `capture_meta.json` (preserves backward compatibility with direct `_cli` invocations)
- [ ] 4.6 Manual sanity check: `.venv/bin/python runner.py --codec h265 --contestant-pgid $$ --duration 2 --fps 1 --output /tmp/_sanity` against a hand-rolled fake frontend (or document why a more targeted unit test stands in for this); inspect `capture_meta.json`  *(deferred — covered by tests/test_cpu_sampler.py for the sampler path; full runner sanity needs a live frontend, run at verify time)*

## 5. Analyzer integration (`analyzer.py`)

- [x] 5.1 After existing metrics computation, check `<screenshots_dir>/capture_meta.json` existence
- [x] 5.2 When present and `capture_meta.json["cpu"]` is non-null, copy it verbatim into the output metrics dict under key `cpu` before writing `<codec>_metrics.json`
- [x] 5.3 When absent or `cpu` is null, do NOT add a `cpu` key (rather than fabricating zeros)
- [x] 5.4 Confirm with a focused test (or extend `tests/test_score_cpu.py`) that the analyzer's pass-through round-trip preserves all 8 cpu sub-fields verbatim

## 6. Shell wiring (`scripts/evaluator.sh`)

- [x] 6.1 In the H.265 runner invocation, append `--contestant-pgid "$(cat "${ROOT_DIR}/results/${RESULTS_SUBDIR}/contestant.pid")"` only when that file exists (so the script remains tolerant of being invoked outside the wrapper's lifecycle)
- [x] 6.2 Do NOT touch the H.264 runner invocation
- [x] 6.3 Confirm `evaluator-host.sh` container path does NOT pass `--contestant-pgid` (the container can't read the host's `/proc` for those PIDs); arrange so the scorer outputs `gate_reason="container_mode_unsupported"`. Most natural place: have the host wrapper invoke `scorer.py --failure-reason container_mode_unsupported` only for the CPU block when running containerized, OR have the scorer detect missing `cpu` field + the container marker file and synthesize the gate. Pick the simpler of the two during 3.7 and document the choice inline
- [ ] 6.4 Re-run `scripts/evaluator-local.sh <team> test_submissions/reference.zip` and confirm `results/<run>/score.json` contains a populated `cpu` block (likely gated by `h265_fps_below_threshold` since reference fails H.265 on the canonical host — that's the expected state)  *(deferred — needs MediaMTX/Chrome/Playwright; synthetic scorer.build_score call confirmed identical score.json shape)*

## 7. Report (`report.py` / report.html template)

- [x] 7.1 Add a CPU summary block to the report template: points, mean_percent, sample_count, sample_window_ms, ncpu, gate state, thresholds_used. Display "gated: <reason>" prominently when applicable
- [x] 7.2 Do NOT add a CPU timeline chart in this change (data contract intentionally summary-only; timeline would require Sampler upgrade — see brainstorm Open Questions)
- [x] 7.3 Eyeball-check one rendered `report.html` against a normal run and one against a gated run

## 8. Documentation sync

- [x] 8.1 Update `CLAUDE.md` Overview: "30-point objective portion" → "40-point objective portion"; add a sentence noting CPU evaluation runs only on H.265, host-native path only
- [x] 8.2 Update `scorer.py` top-of-file docstring: "30-point objective total" → "40-point objective total"; add one line summarizing the CPU sub-score and the 6 tunable constants
- [x] 8.3 Update `openspec/specs/evaluator/spec.md::Purpose` is handled automatically at `/opsx:sync` / `/opsx:archive` time per the project CLAUDE.md rule — DO NOT touch in this change; only confirm during verify that Purpose was updated

## 9. End-to-end validation

- [ ] 9.1 Run `scripts/test.sh` (default mode) and confirm: reference.zip still passes the `total_ge:13` gate (expected: H.264=15, H.265=0, CPU=0 gated → total=15 ≥ 13); negative-case fixtures continue to fail as expected; no test case crashes  *(deferred — full E2E across 7 fixtures × ~30s capture each; run at verify time once Chrome/MediaMTX env is verified)*
- [x] 9.2 Spot-check one `score.json` shape against the spec: 40 max_score, `cpu` block has all 10 keys including the 6-field `thresholds_used`, `objective_total = h264.total + h265.total + cpu.points`
- [x] 9.3 Confirm `pytest tests/ -q` exits green
- [x] 9.4 Run a Python syntax-only re-import smoke: `.venv/bin/python -c "import runner, analyzer, scorer, _cpu_sampler; print('ok')"`
