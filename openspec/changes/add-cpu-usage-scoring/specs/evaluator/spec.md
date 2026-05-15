## ADDED Requirements

### Requirement: Contestant CPU Usage Measurement

During the H.265 capture round, `runner.py` SHALL sample the contestant process group's CPU usage so that `scorer.py` can compute the CPU sub-score documented in the Scoring requirement. The sampler MUST run inside the same Python process as the Playwright capture loop (no sidecar daemon), MUST be active only while the steady-state capture loop is running (excluding `start.sh` warm-up, the readiness wait, and post-capture cleanup), and MUST use only the Python standard library (no new package dependency).

`runner.py` SHALL accept an optional `--contestant-pgid INT` argument. When provided AND `--codec` is `h265`, the runner SHALL start a daemon sampler thread before the capture loop begins and stop it immediately after the loop ends. When `--contestant-pgid` is absent the runner SHALL behave exactly as before this change (no sampling, no `capture_meta.json` written), preserving backward compatibility for direct `_cli` invocations.

`runner.py` SHALL ALSO accept an optional `--cpu-sample-hz FLOAT` (debug-only; scheduled for retirement once a default is calibrated). When absent, the sampler SHALL use `_cpu_sampler.DEFAULT_SAMPLE_HZ` (1.0 Hz at introduction).

The sampler SHALL enumerate `/proc/[0-9]*/stat` on each tick and include any process belonging to **either** of two trees, with PID deduplication so a process matching both is counted once:

  1. **Contestant session tree** — every PID whose stat field-6 (`session`) equals the supplied PGID. This works because `scripts/_contestant_lifecycle.sh::clx_start_contestant` launches the contestant under `setsid`, so the session leader's PID equals the process group ID. Catches any server-side worker the contestant forks under its own session.

  2. **Playwright Chrome process tree** (optional) — every PID reachable from `extra_root_pid` via stat field-4 (`ppid`) descent, inclusive of the root. `runner.py` SHALL pluck `extra_root_pid` from Playwright's private API path `browser._impl_obj._connection._transport._proc.pid` after `chromium.launch(...)` and pass it to the sampler. On `AttributeError` (e.g. Playwright SDK bump changes the internal path) the runner SHALL fall back to `extra_root_pid=None` so sampling degrades to tree (1) alone and records that fact in audit fields. This tree exists because client-side-decode contestant designs (wasm / WebCodecs) run their decoder inside Chrome processes that are NOT in the contestant's PGID subtree; without this union, such contestants register near-zero CPU and bypass the sub-score entirely.

The sampler SHALL by default identify and **exclude** the Chrome GPU process from the union. A PID is classified as the Chrome GPU process when `/proc/<pid>/cmdline` (read as raw bytes) contains either `--gpu-preferences=` or `--type=gpu-process` as a substring; substring containment is required because Chrome rewrites its `/proc/<pid>/cmdline` into a single space-separated string via `prctl(PR_SET_MM_*)`, so the naive `split(b"\x00")` + `startswith(...)` approach silently fails to detect any Chrome subprocess type. Excluded PIDs SHALL be permanently dropped from the baseline, deltas, and per-PID attribution. The Sampler SHALL accept an `exclude_chrome_gpu: bool` parameter defaulting to `True`; the boolean is recorded in `capture_meta.json.cpu.exclude_chrome_gpu` and the excluded PID list in `capture_meta.json.cpu.excluded_gpu_pids` so audit can verify the filter applied to a given run. The exclusion compensates for headless Chrome's SwiftShader software rasterisation (the `--ozone-platform=headless` config has no real GPU pipeline wired in, so all GPU command execution lands on CPU as the GPU process; an unfiltered union assigns this environmental noise to the contestant).

The sampler SHALL accumulate `(utime + stime)` jiffies across all included processes, treat read errors on vanished PIDs as zero-delta (not an error), and use the first observation of a newly appeared PID as its baseline so historical CPU is not retroactively charged.

The sampler SHALL compute `mean_percent = Σ Δjiffies / (Δwall_seconds · ncpu · CLK_TCK) · 100`, where `ncpu = os.cpu_count()` and `CLK_TCK = os.sysconf("SC_CLK_TCK")`. The normalization basis SHALL be the total of all cores (per-core saturation = 100% ÷ ncpu).

When sampling completes, `runner.py` SHALL write `<screenshots_dir>/capture_meta.json` containing at least: `codec`, `capture_started_at_epoch`, `capture_ended_at_epoch`, and a `cpu` sub-object with `mean_percent`, `sample_count`, `sample_window_ms`, `ncpu`, `clk_tck`, `normalization` (constant string `"all_cores_total"`), `pgid`, `extra_root_pid`, `sample_hz_used`, `exclude_chrome_gpu`, `excluded_gpu_pids` (list of PIDs filtered out as GPU process), and `per_process_top` (top-N jiffies-burners with `pid` / `label` / `ppid` / `threads` / `cpu_jiffies` / `percent_of_union` for diagnostic dump). When the sampler collected fewer than `scorer.CPU_MIN_SAMPLES` samples or failed to start, `cpu` SHALL be `null` in `capture_meta.json` and the scoring pipeline SHALL treat this as `gate_reason="sampler_no_data"`.

`analyzer.py` SHALL pass the `cpu` sub-object through to `<output>/h265_metrics.json` verbatim when `<screenshots>/capture_meta.json` exists, performing no CPU-related computation of its own. When the file is absent or `cpu` is null, the analyzer SHALL omit the `cpu` field from `h265_metrics.json` (rather than fabricating zero values).

The CPU measurement pipeline is host-native only. Container-based execution paths (`scripts/evaluator-host.sh` and the portable OCI bundle) cannot read the contestant's host `/proc` from inside the container's PID namespace; in those paths the scoring pipeline SHALL omit sampling and report `gate_reason="container_mode_unsupported"`, `points=0` in `score.json.cpu`.

#### Scenario: Sampler activates only on H.265 with a PGID

- **WHEN** `runner.py --codec h265 --contestant-pgid <P>` is invoked and the contestant frontend reaches readiness
- **THEN** the runner starts the sampler thread immediately before the steady-state capture loop, stops it immediately after the loop ends, writes `<screenshots_dir>/capture_meta.json` with a non-null `cpu` block containing `mean_percent`, `sample_count >= CPU_MIN_SAMPLES`, `sample_window_ms` approximately equal to the capture duration, `normalization="all_cores_total"`, `pgid=<P>`, and `sample_hz_used` equal to the configured rate

#### Scenario: Sampler is a no-op for H.264

- **WHEN** `runner.py --codec h264 --contestant-pgid <P>` is invoked
- **THEN** no sampler thread is started, no `capture_meta.json` is written, and the H.264 metrics output is identical to a run invoked without `--contestant-pgid`

#### Scenario: Missing PGID preserves backward compatibility

- **WHEN** `runner.py --codec h265` is invoked without `--contestant-pgid`
- **THEN** no sampler thread is started, no `capture_meta.json` is written, `h265_metrics.json` contains no `cpu` field, and `score.json.cpu` reports `gate_reason="sampler_no_data"` with `points=0`

#### Scenario: PGID enumerates the full contestant subtree

- **WHEN** the contestant's `start.sh` forks additional processes (relay backend, decoder worker, browser tab) that inherit the session set by `setsid`
- **THEN** the sampler enumerates `/proc/[0-9]*/stat`, includes every process whose field-6 `session` matches the supplied PGID, and the reported `mean_percent` reflects CPU spent by the whole contestant subtree — not just the session leader

#### Scenario: Processes vanishing or appearing mid-capture do not corrupt the mean

- **WHEN** a contestant subprocess exits mid-capture, or a new subprocess is forked mid-capture
- **THEN** the vanished PID's last-observed jiffies remain in the running total without raising an error, the newly appeared PID's first observation establishes a baseline and only subsequent deltas are accumulated, and the final `mean_percent` is a valid normalized value (no negative numbers, no division by zero)

#### Scenario: Container path reports unsupported without crashing

- **WHEN** the evaluator is invoked via `scripts/evaluator-host.sh` (container path) and the container cannot enumerate the host contestant's PIDs
- **THEN** the pipeline completes without raising and writes `score.json` containing a `cpu` block with `points=0`, `gated=true`, `gate_reason="container_mode_unsupported"`, and `mean_percent=null`

#### Scenario: Sampler debug rate is honored

- **WHEN** `runner.py --codec h265 --contestant-pgid <P> --cpu-sample-hz 5.0` is invoked
- **THEN** the sampler ticks at approximately 5 Hz, `capture_meta.json.cpu.sample_hz_used` is `5.0`, and `score.json.cpu.thresholds_used.sample_hz` is `5.0`

#### Scenario: Sampler also follows the Playwright Chrome subtree

- **WHEN** a contestant fans out raw H.265 NALUs and delegates decoding to a wasm worker inside the Chrome page (no decode in the contestant's own session tree)
- **THEN** the sampler's union still picks up the renderer / browser / utility process CPU via `extra_root_pid` ppid descent, `capture_meta.json.cpu.extra_root_pid` records the driver PID it followed, and `mean_percent` reflects the contestant's effective rendering cost — not the near-zero value the contestant's own PGID would report in isolation

#### Scenario: Chrome GPU process is excluded by default

- **WHEN** sampling enumerates the Chrome subtree on a host without a real GPU pipeline (default `--ozone-platform=headless` run, SwiftShader doing software rasterisation)
- **THEN** the GPU process PID — identified via `--gpu-preferences=` / `--type=gpu-process` substring match against its `/proc/<pid>/cmdline` — is excluded from the baseline, deltas, and per-PID attribution; `capture_meta.json.cpu.exclude_chrome_gpu` is `true` and the filtered PID appears in `excluded_gpu_pids`; `mean_percent` reflects only renderer + browser + utility + contestant work, not SwiftShader software rasterisation noise

#### Scenario: Chrome cmdline-rewrite layout is handled

- **WHEN** the sampler reads `/proc/<pid>/cmdline` for a Chrome subprocess that has rewritten its argv into a single space-separated string via `prctl(PR_SET_MM_*)` (the common runtime layout)
- **THEN** the GPU detection function still matches `--gpu-preferences=` via substring containment and correctly classifies the subprocess; a naive `cmdline.split(b"\x00")[i].startswith(b"--gpu-preferences=")` would silently miss every such subprocess and is explicitly NOT used

#### Scenario: Playwright private-API failure degrades safely

- **WHEN** a Playwright SDK upgrade renames or removes the path `browser._impl_obj._connection._transport._proc`
- **THEN** `runner.py` catches `AttributeError`, passes `extra_root_pid=None` to the sampler, and writes `capture_meta.json.cpu.extra_root_pid = null` so the audit field surfaces the regression; the sampler degrades to the contestant-session-only tree rather than raising

---

## MODIFIED Requirements

### Requirement: Scoring

`scorer.py` SHALL accept `--h264`, `--h265`, `--output`, and `--report`; score each codec independently for up to 15 points (10 correctness + 5 FPS); compute an additional 0–10 point CPU sub-score based on contestant CPU usage measured during the H.265 round (see the Contestant CPU Usage Measurement requirement); and produce a `score.json` containing per-codec details, a top-level `cpu` block, `objective_total`, and `max_score` of `40`.

**Correctness** (unchanged): full 10 when `watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`; partial 5 when `watermark_recognition_rate >= 0.80` AND `mean_ssim >= 0.75`; otherwise 0.

**FPS** (unchanged): expected `30` for H.264 and `25` for H.265; full 5 when `|measured_fps - expected| <= 1.0`; partial 3 when `<= 3.0`; otherwise 0.

**CPU** (new): `scorer.score_cpu(mean_cpu_percent, measured_h265_fps, expected_h265_fps)` SHALL return an integer in `[0, 10]` together with a nullable `gate_reason` string, evaluated in this order:

1. Gate: if `measured_h265_fps / expected_h265_fps < CPU_GATE_H265_FPS_RATIO` (default `0.25`), return `(0, "h265_fps_below_threshold")`.
2. If `mean_cpu_percent` is unavailable (analyzer omitted the field, or `capture_meta.json.cpu` was null), return `(0, "sampler_no_data")`.
3. If `mean_cpu_percent <= CPU_FULL_THRESHOLD_PERCENT` (default `5.0`), return `(10, None)`.
4. If `mean_cpu_percent > CPU_ZERO_THRESHOLD_PERCENT` (default `20.0`), return `(0, None)`.
5. Otherwise return `(round((CPU_ZERO_THRESHOLD_PERCENT - mean_cpu_percent) / (CPU_ZERO_THRESHOLD_PERCENT - CPU_PARTIAL_START_PERCENT) * 10), None)`, clamped to `[0, 10]`. `CPU_PARTIAL_START_PERCENT` defaults to `6.0`.

The six tunables (`CPU_GATE_H265_FPS_RATIO`, `CPU_FULL_THRESHOLD_PERCENT`, `CPU_PARTIAL_START_PERCENT`, `CPU_ZERO_THRESHOLD_PERCENT`, `CPU_MIN_SAMPLES`, `_cpu_sampler.DEFAULT_SAMPLE_HZ`) SHALL be exposed as named module-level constants and the actual values applied to each run SHALL be recorded under `score.json.cpu.thresholds_used` so a contestant audit can verify which thresholds produced the score.

When the H.265 round fails entirely (no `h265_metrics.json` produced, or it lacks the fields the gate inspects), `scorer.py` SHALL still emit a complete `score.json` with `cpu.points=0`, `cpu.gated=true`, `cpu.gate_reason="h265_round_failed"`, and `cpu.mean_percent=null`. When the host-side wrapper fails before the evaluator main body runs, the existing failure `score.json` path SHALL include `cpu.gated=true`, `cpu.gate_reason="host_failure"`, `cpu.points=0`.

`objective_total` SHALL equal `h264.total + h265.total + cpu.points` and SHALL NOT exceed `max_score`.

#### Scenario: Per-codec totals

- **WHEN** scoring completes for a submission
- **THEN** `score.json` includes an H.264 block, an H.265 block, a top-level `cpu` block, an `objective_total` equal to `h264.total + h265.total + cpu.points`, and `max_score = 40`, with the underlying metrics (rates, mean SSIM, measured FPS, CPU mean percent, sample count, thresholds applied) preserved for audit

#### Scenario: Static-frame submission

- **WHEN** a submission renders a single static image so `measured_fps` is near 0
- **THEN** it receives 0 FPS points for that codec while correctness is scored on its own merits; for H.265 specifically, the CPU gate trips on `measured_h265_fps / expected_h265_fps < CPU_GATE_H265_FPS_RATIO` so `cpu.points=0` and `cpu.gate_reason="h265_fps_below_threshold"`

#### Scenario: Fake-overlay submission

- **WHEN** a submission draws a fake canvas overlay that visually resembles the watermark but fails DataMatrix, color block, or SSIM checks
- **THEN** it cannot reach full correctness (10) and may receive 5 or 0 depending on which checks it passes

#### Scenario: CPU full marks at low usage

- **WHEN** the H.265 round earns at least partial FPS credit AND `mean_cpu_percent <= 5.0`
- **THEN** `cpu.points = 10`, `cpu.gated = false`, `cpu.gate_reason = null`, and `cpu.thresholds_used` records every threshold that produced this outcome

#### Scenario: CPU partial credit in the proportional band

- **WHEN** the H.265 round earns at least partial FPS credit AND `mean_cpu_percent` falls within `(5.0, 20.0]`
- **THEN** `cpu.points = round((20 - mean_cpu_percent) / 14 * 10)` clamped to `[0, 10]`, `cpu.gated = false`, and the formula matches the published score table at the integer percent breakpoints (`6→10`, `7→9`, `10→7`, `13→5`, `15→4`, `17→2`, `19→1`, `20→0`)

#### Scenario: CPU zero when usage exceeds the upper limit

- **WHEN** the H.265 round earns at least partial FPS credit AND `mean_cpu_percent > 20.0`
- **THEN** `cpu.points = 0`, `cpu.gated = false`, `cpu.gate_reason = null`, and `cpu.mean_percent` is recorded as-measured for audit (not clipped)

#### Scenario: CPU gated when H.265 fps round fails

- **WHEN** the H.265 round produces `measured_fps / expected_fps < CPU_GATE_H265_FPS_RATIO` (default `0.25`, i.e. `< 6.25` fps against the H.265 expected `25` fps)
- **THEN** `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "h265_fps_below_threshold"`, and `cpu.mean_percent` still records the measured value (or `null` if sampling failed) for diagnostic purposes

#### Scenario: CPU gated when sampler produced no data

- **WHEN** the H.265 capture round ran but `capture_meta.json` is missing OR `capture_meta.json.cpu` is null OR fewer than `CPU_MIN_SAMPLES` samples were collected
- **THEN** `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "sampler_no_data"`, and `cpu.mean_percent = null`

#### Scenario: CPU gated when H.265 round itself failed

- **WHEN** no `h265_metrics.json` was produced (player error, readiness timeout, missing element, etc.)
- **THEN** `score.json` is still emitted with `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "h265_round_failed"`, and `cpu.mean_percent = null`, alongside whatever `h265.reason` the runner recorded

#### Scenario: Thresholds-used audit field

- **WHEN** any `score.json` is produced
- **THEN** `score.json.cpu.thresholds_used` contains exactly the six keys `gate_fps_ratio`, `full_percent`, `partial_start_percent`, `zero_percent`, `min_samples`, `sample_hz`, each set to the value actually applied to that run, so an organizer can replay the score from the metrics alone
