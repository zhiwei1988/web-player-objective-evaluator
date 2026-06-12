## ADDED Requirements

### Requirement: Contestant Bandwidth Limit

The evaluator SHALL enforce a hard egress network bandwidth limit on the IP-layer traffic emitted by each contestant submission process tree. The default effective limit SHALL be `100mbit` (100 Mbit/s); operators MAY override it with `EVALUATOR_CONTESTANT_BANDWIDTH_MAX`, but the evaluator MUST NOT silently run a contestant submission without an effective egress bandwidth limit on the canonical Ubuntu 24.04 host.

The limit SHALL apply to contestant `start.sh` and all descendant processes (relay servers, transmuxers, decoders, Node/Python helpers, any forked child). The limit SHALL NOT apply to evaluator-owned processes such as MediaMTX (including its 16 Mbit/s RTSP source stream), Playwright/Chromium, `runner.py`, `analyzer.py`, `scorer.py`, `report.py`, or cleanup helpers.

Enforcement SHALL be a physical traffic-shaping boundary, not a detection heuristic: the evaluator SHALL classify packets by the originating process's cgroup using the **same cgroup v2 boundary already created for the Contestant Memory Limit** (the systemd transient unit), mark contestant-originated packets via nftables `socket cgroupv2`, and shape the marked traffic on the loopback device with `tc`/`htb` so over-limit traffic is queued or dropped and the contestant's `send()` is back-pressured. A per-process `tc` qdisc on a shared device without cgroup classification SHALL NOT satisfy this requirement, because it cannot distinguish contestant traffic from evaluator traffic on the shared loopback device.

The limit SHALL be a single shared bucket for the whole contestant process tree: all IP-layer egress from the tree SHALL share the one `100mbit` allowance. Inter-process transport that does not traverse the IP layer (unix domain sockets, shared memory, pipes) SHALL NOT be shaped. Over-limit behavior SHALL degrade contestant performance naturally (frame drops lowering measured FPS and SSIM); the evaluator SHALL NOT introduce a new "bandwidth exceeded" violation verdict or zero-score gate for exceeding the bandwidth limit.

#### Scenario: Default bandwidth limit is applied

- **WHEN** `scripts/evaluator.sh` starts a contestant submission and `EVALUATOR_CONTESTANT_BANDWIDTH_MAX` is not set
- **THEN** contestant `start.sh` and its descendants run with their IP-layer egress shaped to an effective limit of `100mbit`
- **THEN** the run artifacts record the effective contestant bandwidth limit

#### Scenario: Environment override is applied

- **WHEN** an organizer runs `scripts/evaluator.sh` with `EVALUATOR_CONTESTANT_BANDWIDTH_MAX=50mbit`
- **THEN** contestant `start.sh` and its descendants run with IP-layer egress shaped to `50mbit`
- **THEN** the run artifacts record `50mbit` as the effective contestant bandwidth limit

#### Scenario: Evaluator-owned traffic is not shaped

- **WHEN** MediaMTX serves the 4K `16mbit` RTSP source stream and Chromium issues HTTP requests during a run
- **THEN** that evaluator-owned traffic is not classified into the contestant shaping class and is not throttled by the contestant bandwidth policy
- **THEN** only traffic originating from processes inside the contestant cgroup is shaped to the effective limit

#### Scenario: Over-limit degrades score without a violation verdict

- **WHEN** a contestant architecture attempts to push decoded frames across the IP layer at a rate exceeding the effective bandwidth limit
- **THEN** the excess traffic is queued or dropped by the shaper, the contestant observes back-pressure, and the resulting frame drops lower `measured_fps` and/or `mean_ssim`
- **THEN** no `decode_forensics.verdict == "violation"` is fabricated for the bandwidth condition and no profile is zeroed solely because the bandwidth limit was reached

#### Scenario: Shared bucket covers forked descendants

- **WHEN** contestant `start.sh` forks child workers that each open IP-layer sockets and together attempt to exceed the effective limit
- **THEN** their combined IP-layer egress is shaped against the single shared `100mbit` allowance
- **THEN** inter-process transport over unix sockets or shared memory between those workers is not shaped

#### Scenario: Bandwidth limit is torn down on cleanup

- **WHEN** an evaluation run ends on any exit path (success, contestant crash, infrastructure failure)
- **THEN** cleanup idempotently removes the loopback `tc` qdisc and nftables marking rules created for the run, leaving no shaping state that would affect a later run
- **THEN** removing already-absent shaping state does not raise an error

### Requirement: Contestant Bandwidth Limiter Preflight

Before starting contestant code, the evaluator SHALL verify that the host can enforce the contestant bandwidth limit. On the canonical Ubuntu 24.04 host, this preflight SHALL require: cgroup v2 (already required by the Contestant Memory Limiter Preflight), the `CAP_NET_ADMIN` capability (or an equivalent privileged delegation) for the evaluator's traffic-control operations, the availability of `tc` and `nft`, and the ability to install a root qdisc on the loopback device. If the preflight fails, the evaluator SHALL fail loudly as an infrastructure failure before invoking contestant `start.sh`.

#### Scenario: Supported host passes preflight

- **WHEN** the evaluator can install a loopback root qdisc and an nftables `socket cgroupv2` marking rule for the contestant cgroup
- **THEN** the evaluator proceeds to extract and start the contestant submission with the bandwidth limit applied

#### Scenario: Unsupported host fails before contestant starts

- **WHEN** `CAP_NET_ADMIN` is unavailable, `tc` or `nft` is missing, or a loopback root qdisc cannot be installed
- **THEN** `scripts/evaluator.sh` exits with an infrastructure failure before invoking contestant `start.sh`
- **THEN** the evaluator does not run the submission without an effective bandwidth limit

#### Scenario: Preflight result is logged

- **WHEN** the bandwidth-limiter preflight passes or fails
- **THEN** `evaluator.log` records the effective bandwidth limit and enough preflight detail for an organizer to diagnose host support

## MODIFIED Requirements

### Requirement: Reference Stream Generation

`prepare_streams.sh` together with `lib/watermark.py` SHALL produce two watermarked MP4 files and matching PNG reference frame sequences before the contest, one per profile defined in `lib/profiles.py::PROFILES`. The **2K profile** SHALL produce `streams/h265_2560_1440.mp4` (`2560x1440`, `20fps`, `30s` duration, `libx265 hvc1 yuv420p` at `4 Mbps`, GOP `40`, `scenecut=0`) with frames under `reference/2k/frame_NNNNN.png`. The **4K profile** SHALL produce `streams/h265_3840_2160.mp4` (`3840x2160`, `20fps`, `30s` duration, `libx265 hvc1 yuv420p` at `16 Mbps`, GOP `40`, `scenecut=0`) with frames under `reference/4k/frame_NNNNN.png`. Each profile SHALL produce `fps * duration_s` reference PNG frames, i.e. `600` frames for the default 20fps/30s profiles. All ProfileSpec-derived parameters (resolution, fps, bitrate, duration, output paths) SHALL come from `PROFILES` rather than from script-local literals. `prepare_streams.sh` SHALL delete legacy `streams/h264_watermarked.mp4`, `streams/h265_watermarked.mp4`, `reference/h264/`, and `reference/h265/` when present, before regenerating, so the two layouts do not coexist.

Reference frame backgrounds SHALL carry deterministic, per-frame-varying high-entropy content (a frame-number-seeded tiled random-color field in `lib/watermark.py`) so that the encoded 4K stream actually approaches its `16 Mbps` target and imposes a real per-frame decode load, rather than collapsing to a near-lossless ~8 Mbps as flat synthetic backgrounds do. The high-entropy content SHALL be deterministic (same frame number → byte-identical PNG across regenerations) and SHALL be drawn beneath the four watermark signals so frame-number, timecode, color blocks, and DataMatrix remain decodable. The background spatial scale SHALL be chosen so the content survives the evaluator's `1280x720` capture-and-resize SSIM path with full-correctness SSIM still achievable by a faithful decoder.

#### Scenario: Watermark content per frame

- **WHEN** any reference frame `N` is generated for any profile
- **THEN** the frame contains a high-contrast 5-digit zero-padded frame number block in the top-left, a `HH:MM:SS.mmm` timecode in the top-right, four solid color blocks `(255,0,0)`, `(0,255,0)`, `(0,0,255)`, `(255,255,255)` along the bottom, and a DataMatrix code in the bottom-right encoding the integer `N`

#### Scenario: Idempotent regeneration

- **WHEN** `prepare_streams.sh` is rerun on a host where outputs already exist
- **THEN** it overwrites the MP4 files and the reference frame directories deterministically so two runs from the same code produce byte-identical PNGs and equivalent MP4s for the same profile settings

#### Scenario: Legacy assets removed

- **WHEN** `prepare_streams.sh` is run on a host where `streams/h264_watermarked.mp4`, `streams/h265_watermarked.mp4`, `reference/h264/`, or `reference/h265/` exists from a prior layout
- **THEN** those legacy paths are removed before new generation begins, leaving only the 2K and 4K profile outputs

#### Scenario: Profile frame count follows 20fps source

- **WHEN** streams are generated with the default `PROFILES`
- **THEN** `reference/2k/` and `reference/4k/` each contain 600 numbered frames, and both generated MP4 streams report `20fps`

#### Scenario: 4K stream is encoded at the hardened bitrate

- **WHEN** the 4K stream is generated from `PROFILES["4k"]`
- **THEN** `streams/h265_3840_2160.mp4` is encoded with `libx265` at `16 Mbps` as derived from `PROFILES["4k"].bitrate`, not a script-local literal, and the 2K stream remains at `4 Mbps`

#### Scenario: 4K stream actually carries the hardened bitrate

- **WHEN** the regenerated `streams/h265_3840_2160.mp4` is probed
- **THEN** its measured average bitrate is materially above the ~8 Mbps that flat synthetic backgrounds produce (i.e. the high-entropy background fills the encoder toward the 16 Mbps target), confirming the difficulty increase is real and not merely a raised VBV ceiling

#### Scenario: High-entropy background is deterministic and decodable

- **WHEN** the same frame number is rendered twice by `lib/watermark.py`
- **THEN** the two PNGs are byte-identical, and the frame-number block, timecode, four color blocks, and DataMatrix all remain decodable on top of the high-entropy background

### Requirement: Scoring

`scorer.py` SHALL accept one or more `--metrics PROFILE=PATH` arguments (one per profile, e.g. `--metrics 2k=results/.../2k_metrics.json --metrics 4k=results/.../4k_metrics.json`), `--output`, and `--report`; score 2K for up to 10 points (5 correctness + 5 FPS), score 4K for up to 15 points (5 correctness + 10 FPS), compute an additional 0-5 point CPU sub-score based on contestant CPU usage measured during the **4K profile** round (see the Contestant CPU Usage Measurement requirement); and produce a `score.json` containing one block per profile (keys `2k`, `4k`), a top-level `cpu` block, a top-level `gate` block, `objective_total`, and `max_score` of `30`.

**Decode-path gate** (first, per profile, UNCHANGED): when the profile's `metrics.decode_forensics.verdict == "violation"`, that profile's `correctness_points` AND `fps_points` SHALL both be `0` regardless of measured rates or FPS, and the profile block SHALL include a `decode_path` sub-block (`verdict`, `checks`, `evidence`). When the violating profile is the CPU-sampled profile (`4k` by default), the CPU block SHALL additionally be set to `points=0, gated=true, gate_reason="decode_path_violation"`. A verdict of `ok`, `inconclusive`, or an absent `decode_forensics` block SHALL NOT affect scoring (fail-open); an `inconclusive` verdict on any profile SHALL set top-level `score.json.review_required = true`.

**Correctness** (per profile): full 5 when `watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`; partial 2 when `watermark_recognition_rate >= 0.80` AND `mean_ssim >= 0.75`; otherwise 0. This applies to both 2K and 4K. 4K correctness SHALL remain scored even when 4K FPS is low — EXCEPT when that profile's decode-path verdict is `violation`.

**FPS** (linear absolute, both profiles): each profile's expected FPS SHALL come from `PROFILES[profile].fps` and default to `20`. Each profile's per-profile FPS full score SHALL come from `FPS_LINEAR_FULL_SCORE_BY_PROFILE` (`{"2k": 5, "4k": 10}`) as the single source of truth. `score.json.<profile>.fps_points` SHALL equal `round(min(max(measured_fps, 0) / expected_fps, 1.0) * FPS_LINEAR_FULL_SCORE_BY_PROFILE[profile], 2)`. Each profile block SHALL record `fps_scoring_mode = "linear_absolute"`, `fps_linear_full_score` (5 for 2K, 10 for 4K), and `expected_fps`. No profile block SHALL carry `fps_full_threshold_used` or `fps_partial_threshold_used`; threshold-based 2K FPS scoring and the `FPS_FULL_RATIO_BY_PROFILE` / `FPS_PARTIAL_RATIO_BY_PROFILE` tables are removed.

**CPU**: `scorer.score_cpu(...)` SHALL return a value in `[0, 5]` with a nullable `gate_reason`, computed exactly as before (fps floor → `sampler_no_data` → full/zero/partial bands). The CPU sub-score SHALL be measured on the profile whose `ProfileSpec.cpu_sampled` flag is true (`4k` by default), and `scorer.CPU_PROFILE` SHALL be derived from that flag rather than hard-coded. The CPU band thresholds (`CPU_FULL_THRESHOLD_PERCENT`, `CPU_PARTIAL_START_PERCENT`, `CPU_ZERO_THRESHOLD_PERCENT`, `CPU_GATE_FPS_RATIO`) SHALL be calibrated for the CPU-sampled profile's load and SHALL remain module-level constants echoed into `score.json.cpu.thresholds_used`. The CPU block SHALL record `measured_on_profile` and `gate_profile` equal to the CPU-sampled profile name (`4k` by default), `expected_fps`, `measured_fps`, `thresholds_used`, `mean_percent`, `sample_count`, and the existing audit fields.

**Level-0 gate conditions the total** (see the Level-0 Gate (Decode Correctness) requirement). The gate passes iff `2k.correctness_points == 5` AND `4k.correctness_points == 5`.

- **When the gate PASSES**, each profile's `total` SHALL equal `correctness_points + fps_points`, the CPU sub-score SHALL be scored normally, and `objective_total` SHALL equal `2k.total + 4k.total + cpu.points` (the full 30-point scheme).
- **When the gate FAILS**, level-1 SHALL NOT contribute: each profile's `total` SHALL equal its `correctness_points` alone (FPS still computed and recorded under `fps_points`, but excluded from `total`), the CPU block SHALL be set to `points=0, gated=true, gate_reason="gate_failed"` (UNLESS a more specific reason already applies — `decode_path_violation`, `4k_fps_below_threshold`, `sampler_no_data`, `4k_round_failed`, `container_mode_unsupported`, `host_failure` — which is retained), and `objective_total` SHALL equal `2k.correctness_points + 4k.correctness_points`.

`max_score` SHALL remain `30`. Because reaching `30` requires both full correctness (gate pass) and full level-1 performance, decode correctness alone caps the total at `≤ 10` ("level-0 has no full marks"). `objective_total` MAY be fractional (FPS is fractional for both profiles) and SHALL be rounded consistently for display. No profile block SHALL carry `reason = "skipped_gate_failed"` (both profiles are always captured per the Evaluator Entry Script requirement).

#### Scenario: Per-profile totals and the gate block

- **WHEN** scoring completes for a submission
- **THEN** `score.json` includes a `2k` block, a `4k` block, a top-level `cpu` block, a top-level `gate` block, `max_score = 30`, and an `objective_total`; the underlying metrics (rates, mean SSIM, measured FPS, `fps_points`, CPU mean percent, sample count, thresholds/formulas) are preserved for audit; the keys `h264` / `h265` SHALL NOT appear

#### Scenario: Perfect run scores the full 30

- **WHEN** both profiles reach full correctness and full FPS and CPU is at full marks (`2k`: 5 + 5, `4k`: 5 + 10, `cpu`: 5)
- **THEN** `score.json.gate.passed = true`, `score.json.2k.total = 10`, `score.json.4k.total = 15`, `score.json.cpu.points = 5`, and `objective_total = 30`

#### Scenario: Gate passes but performance is poor

- **WHEN** both profiles reach full correctness (`gate.passed = true`) but `2k.fps_points = 0`, `4k.fps_points = 0`, and CPU scores `0`
- **THEN** `score.json.2k.total = 5`, `score.json.4k.total = 5`, `objective_total = 10`, and the FPS/CPU values are recorded — the gate is open, the level-1 points were simply not earned

#### Scenario: Gate fails — only correctness counts

- **WHEN** `2k.correctness_points = 5` but `4k.correctness_points = 2` (so the gate fails)
- **THEN** `score.json.gate.passed = false`, `score.json.2k.total = 5`, `score.json.4k.total = 2`, the `fps_points` fields are still recorded but excluded from the totals, `score.json.cpu.points = 0` with `cpu.gated = true` and `cpu.gate_reason = "gate_failed"`, and `objective_total = 7`

#### Scenario: Decode-path violation fails the gate and keeps decode-path precedence

- **WHEN** the 4K round's `decode_forensics.verdict == "violation"` (so `4k.correctness_points = 0` and the gate fails) and 4K is the CPU-sampled profile
- **THEN** `objective_total = 2k.correctness_points` (4K contributes 0), level-1 is not scored, and the 4K `violation` keeps `cpu.gate_reason = "decode_path_violation"` rather than `"gate_failed"`

#### Scenario: OK or inconclusive verdict does not change scoring

- **WHEN** a profile's decode-path verdict is `ok`, `inconclusive`, or absent
- **THEN** that profile is scored on its measured metrics exactly as without forensics; an `inconclusive` verdict additionally sets `score.json.review_required = true`

#### Scenario: Fake-overlay submission

- **WHEN** a submission draws a fake canvas overlay that fails DataMatrix, color block, or SSIM checks
- **THEN** it cannot reach full correctness (5) on that profile, the gate fails, and level-1 is not scored

#### Scenario: 2K FPS is scored linearly out of 5

- **WHEN** the 2K round measures `measured_fps = 10` against `expected_fps = 20`
- **THEN** `score.json.2k.fps_points = round(min(10 / 20, 1.0) * 5, 2) = 2.5`, the 2K block records `fps_scoring_mode = "linear_absolute"` and `fps_linear_full_score = 5`, and no `fps_full_threshold_used` / `fps_partial_threshold_used` field is present

#### Scenario: FPS at or above expected caps at the profile full score

- **WHEN** a profile measures `measured_fps >= expected_fps`
- **THEN** that profile's `fps_points` equals its `fps_linear_full_score` exactly (5 for 2K, 10 for 4K), never more

#### Scenario: Missing profile entry fails loudly

- **WHEN** `scorer.score_fps` is invoked with a profile name absent from `PROFILES`
- **THEN** `score_fps` raises a clear error (e.g. `KeyError`) rather than silently scoring against a default

### Requirement: Contestant CPU Usage Measurement

During the **4K profile** capture round, `runner.py` SHALL sample the contestant process group's CPU usage so that `scorer.py` can compute the CPU sub-score documented in the Scoring requirement. The sampler MUST run inside the same Python process as the Playwright capture loop (no sidecar daemon), MUST be active only while the steady-state capture loop is running (excluding `start.sh` warm-up, the readiness wait, and post-capture cleanup), and MUST use only the Python standard library (no new package dependency).

`runner.py` SHALL accept an optional `--contestant-pgid INT` argument. When provided AND `--profile` resolves to a `ProfileSpec` whose `cpu_sampled` flag is `True` (true for `4k` only, by default), the runner SHALL start a daemon sampler thread before the capture loop begins and stop it immediately after the loop ends. When `--contestant-pgid` is absent OR the active profile's `cpu_sampled` flag is `False`, the runner SHALL behave exactly as before sampling was introduced (no sampling, no `capture_meta.json` written), preserving backward compatibility for direct `_cli` invocations and the 2K round.

`runner.py` SHALL ALSO accept an optional `--cpu-sample-hz FLOAT` (debug-only; scheduled for retirement once a default is calibrated). When absent, the sampler SHALL use `_cpu_sampler.DEFAULT_SAMPLE_HZ`.

The sampler SHALL enumerate `/proc/[0-9]*/stat` on each tick and include any process belonging to **either** of two trees, with PID deduplication so a process matching both is counted once:

  1. **Contestant session tree** — every PID whose stat field-6 (`session`) equals the supplied PGID. This works because `scripts/_contestant_lifecycle.sh::clx_start_contestant` launches the contestant under `setsid`, so the session leader's PID equals the process group ID. Catches any server-side worker the contestant forks under its own session.

  2. **Playwright Chrome process tree** (optional) — every PID reachable from `extra_root_pid` via stat field-4 (`ppid`) descent, inclusive of the root. `runner.py` SHALL pluck `extra_root_pid` from Playwright's private API path `browser._impl_obj._connection._transport._proc.pid` after `chromium.launch(...)` and pass it to the sampler. On `AttributeError` (e.g. Playwright SDK bump changes the internal path) the runner SHALL fall back to `extra_root_pid=None` so sampling degrades to tree (1) alone and records that fact in audit fields. This tree exists because client-side-decode contestant designs (wasm / WebCodecs) run their decoder inside Chrome processes that are NOT in the contestant's PGID subtree; without this union, such contestants register near-zero CPU and bypass the sub-score entirely.

The sampler SHALL by default identify and **exclude** the Chrome GPU process from the union. A PID is classified as the Chrome GPU process when `/proc/<pid>/cmdline` (read as raw bytes) contains either `--gpu-preferences=` or `--type=gpu-process` as a substring; substring containment is required because Chrome rewrites its `/proc/<pid>/cmdline` into a single space-separated string via `prctl(PR_SET_MM_*)`, so the naive `split(b"\x00")` + `startswith(...)` approach silently fails to detect any Chrome subprocess type. Excluded PIDs SHALL be permanently dropped from the baseline, deltas, and per-PID attribution. The Sampler SHALL accept an `exclude_chrome_gpu: bool` parameter defaulting to `True`; the boolean is recorded in `capture_meta.json.cpu.exclude_chrome_gpu` and the excluded PID list in `capture_meta.json.cpu.excluded_gpu_pids` so audit can verify the filter applied to a given run.

The sampler SHALL accumulate `(utime + stime)` jiffies across all included processes, treat read errors on vanished PIDs as zero-delta (not an error), and use the first observation of a newly appeared PID as its baseline so historical CPU is not retroactively charged.

The sampler SHALL compute `mean_percent = Σ Δjiffies / (Δwall_seconds · ncpu · CLK_TCK) · 100`, where `ncpu = os.cpu_count()` and `CLK_TCK = os.sysconf("SC_CLK_TCK")`. The normalization basis SHALL be the total of all cores (per-core saturation = 100% ÷ ncpu).

When sampling completes, `runner.py` SHALL write `<screenshots_dir>/capture_meta.json` containing at least: `profile` (the active profile name, `"4k"`), `capture_started_at_epoch`, `capture_ended_at_epoch`, and a `cpu` sub-object with `mean_percent`, `sample_count`, `sample_window_ms`, `ncpu`, `clk_tck`, `normalization` (constant string `"all_cores_total"`), `pgid`, `extra_root_pid`, `sample_hz_used`, `exclude_chrome_gpu`, `excluded_gpu_pids`, and `per_process_top`. When the sampler collected fewer than `scorer.CPU_MIN_SAMPLES` samples or failed to start, `cpu` SHALL be `null` in `capture_meta.json` and the scoring pipeline SHALL treat this as `gate_reason="sampler_no_data"`.

`analyzer.py` SHALL pass the `cpu` sub-object through to `<output>/4k_metrics.json` verbatim when `<screenshots>/capture_meta.json` exists, performing no CPU-related computation of its own. When the file is absent or `cpu` is null, the analyzer SHALL omit the `cpu` field from `4k_metrics.json` (rather than fabricating zero values).

#### Scenario: Sampler activates only on 4K with a PGID

- **WHEN** `runner.py --profile 4k --contestant-pgid <P>` is invoked and the contestant frontend reaches readiness
- **THEN** the runner starts the sampler thread immediately before the steady-state capture loop, stops it immediately after the loop ends, writes `<screenshots_dir>/capture_meta.json` with `profile = "4k"` and a non-null `cpu` block containing `mean_percent`, `sample_count >= CPU_MIN_SAMPLES`, `sample_window_ms` approximately equal to the capture duration, `normalization="all_cores_total"`, `pgid=<P>`, and `sample_hz_used` equal to the configured rate

#### Scenario: Sampler is a no-op for 2K

- **WHEN** `runner.py --profile 2k --contestant-pgid <P>` is invoked
- **THEN** no sampler thread is started, no `capture_meta.json` is written, and the 2K metrics output contains no `cpu` field

#### Scenario: Missing PGID preserves backward compatibility

- **WHEN** `runner.py --profile 4k` is invoked without `--contestant-pgid`
- **THEN** no sampler thread is started, no `capture_meta.json` is written, `4k_metrics.json` contains no `cpu` field, and `score.json.cpu` reports `gate_reason="sampler_no_data"` with `points=0`

#### Scenario: PGID enumerates the full contestant subtree

- **WHEN** the contestant's `start.sh` forks additional processes (relay backend, decoder worker, browser tab) that inherit the session set by `setsid`
- **THEN** the sampler enumerates `/proc/[0-9]*/stat`, includes every process whose field-6 `session` matches the supplied PGID, and the reported `mean_percent` reflects CPU spent by the whole contestant subtree — not just the session leader

#### Scenario: Processes vanishing or appearing mid-capture do not corrupt the mean

- **WHEN** a contestant subprocess exits mid-capture, or a new subprocess is forked mid-capture
- **THEN** the vanished PID's last-observed jiffies remain in the running total without raising an error, the newly appeared PID's first observation establishes a baseline and only subsequent deltas are accumulated, and the final `mean_percent` is a valid normalized value (no negative numbers, no division by zero)

#### Scenario: Sampler debug rate is honored

- **WHEN** `runner.py --profile 4k --contestant-pgid <P> --cpu-sample-hz 5.0` is invoked
- **THEN** the sampler ticks at approximately 5 Hz, `capture_meta.json.cpu.sample_hz_used` is `5.0`, and `score.json.cpu.thresholds_used.sample_hz` is `5.0`

#### Scenario: Sampler also follows the Playwright Chrome subtree

- **WHEN** a contestant fans out raw H.265 NALUs and delegates decoding to a wasm worker inside the Chrome page
- **THEN** the sampler's union still picks up the renderer / browser / utility process CPU via `extra_root_pid` ppid descent, `capture_meta.json.cpu.extra_root_pid` records the driver PID it followed, and `mean_percent` reflects the contestant's effective rendering cost

#### Scenario: Chrome GPU process is excluded by default

- **WHEN** sampling enumerates the Chrome subtree on a host without a real GPU pipeline
- **THEN** the GPU process PID is excluded from the baseline, deltas, and per-PID attribution; `capture_meta.json.cpu.exclude_chrome_gpu` is `true` and the filtered PID appears in `excluded_gpu_pids`

#### Scenario: Chrome cmdline-rewrite layout is handled

- **WHEN** the sampler reads `/proc/<pid>/cmdline` for a Chrome subprocess that has rewritten its argv into a single space-separated string via `prctl(PR_SET_MM_*)` (the common runtime layout)
- **THEN** the GPU detection function still matches `--gpu-preferences=` via substring containment and correctly classifies the subprocess; a naive `cmdline.split(b"\x00")[i].startswith(b"--gpu-preferences=")` would silently miss every such subprocess and is explicitly NOT used

#### Scenario: Playwright private-API failure degrades safely

- **WHEN** a Playwright SDK upgrade renames or removes the path `browser._impl_obj._connection._transport._proc`
- **THEN** `runner.py` catches `AttributeError`, passes `extra_root_pid=None` to the sampler, and writes `capture_meta.json.cpu.extra_root_pid = null` so the audit field surfaces the regression; the sampler degrades to the contestant-session-only tree rather than raising

### Requirement: Report Generation

`report.py` SHALL generate an internal-only `report.html` per run, containing: a top summary with `objective_total` and per-profile subtotals (`2k` and `4k`); a gallery of suspicious screenshots (watermark recognition failures, color block failures, samples with SSIM `< 0.7`); a frame-number-over-time chart for each profile; an SSIM histogram for each profile; capture-throughput diagnostics for each profile; CPU scoring details identifying that CPU was measured on the 4K profile; the effective contestant bandwidth limit applied to the run; the 2K and 4K linear absolute FPS formulas and applied expected FPS; and relative-path links to the raw artifacts. The report MUST NOT be exposed to contestants by default.

#### Scenario: Report includes audit evidence

- **WHEN** scoring completes
- **THEN** `report.html` opens in a browser, renders all charts and galleries from local files only (no network calls), shows capture sampling FPS and capture overrun diagnostics for each completed profile, shows CPU measured on profile `4k`, shows the effective contestant bandwidth limit, shows the 2K and 4K linear FPS formulas, and links to `2k_screenshots/`, `4k_screenshots/`, both metrics JSONs, and `score.json`

#### Scenario: Empty failure gallery

- **WHEN** a submission passes every check
- **THEN** the report still renders, with the suspicious-screenshot gallery showing zero entries and a clear "no failures" message

### Requirement: Orchestration and Cleanup

`scripts/evaluator.sh <team_id> <submission_zip>` SHALL execute the in-body pipeline portion of the evaluator (after host-side preparation succeeds per Requirement `Evaluator Entry Script`) in this order: start MediaMTX via `scripts/start_rtsp.sh` listening on port `554`; health-check both `rtsp://127.0.0.1:554/test/h265_2560_1440` and `rtsp://127.0.0.1:554/test/h265_3840_2160` via `scripts/health_check.sh`; run the 2K capture (30s) and the 4K capture (30s) in fresh Playwright Chromium contexts via `runner.py`, passing `--contestant-pgid` only for the profile whose `ProfileSpec.cpu_sampled` flag is true (4K by default); analyze both screenshot directories with `analyzer.py`; score with `scorer.py` (passing `--metrics 2k=... --metrics 4k=...`) and generate `report.html` with `report.py`; terminate MediaMTX; print the final `score.json` to the original stdout. Before invoking contestant `start.sh` during host-side preparation, the script SHALL apply the contestant bandwidth limit to the contestant cgroup (see the Contestant Bandwidth Limit requirement); the bandwidth limiter SHALL be torn down by the cleanup path. A cleanup function and shell traps MUST be defined before any step that could fail, so cleanup runs on any exit path and idempotently removes both the contestant process group and the contestant bandwidth-shaping state. Host-side concerns (zip extraction, invocation of contestant `start.sh` / `stop.sh`, contestant process-group cleanup, single-instance lock, port `8080` precheck, bandwidth-limiter setup/teardown) are owned by this same script under Requirement `Evaluator Entry Script`; the in-body pipeline described here SHALL run only after that preparation has succeeded. There SHALL NOT exist any separate wrapper script (`evaluator-host.sh`, `evaluator-local.sh`, or otherwise) that invokes this script; the script is the single entry. MediaMTX SHALL be a per-invocation process owned by this script and SHALL NOT be assumed to exist as a host-resident daemon shared across runs. The script SHALL iterate the profiles defined in `lib/profiles.py::PROFILES`; adding a new profile MUST NOT require new branches in this script.

#### Scenario: Successful in-body run

- **WHEN** the host-side preparation phase of `scripts/evaluator.sh` has confirmed that port `8080` is free, extracted the submission zip, applied the contestant bandwidth limit, started the contestant `start.sh`, and observed the contestant frontend become reachable
- **THEN** the in-body pipeline completes, `score.json` is printed to stdout, and all artifact files (`2k_screenshots/`, `4k_screenshots/`, `2k_metrics.json`, `4k_metrics.json`, `score.json`, `report.html`, `evaluator.log`) are present under the results directory

#### Scenario: CPU PGID follows the sampled profile

- **WHEN** `scripts/evaluator.sh` invokes `runner.py` for the default 2K and 4K profiles
- **THEN** it passes `--contestant-pgid` to the 4K invocation and does not pass it to the 2K invocation, because `PROFILES["4k"].cpu_sampled = True` and `PROFILES["2k"].cpu_sampled = False`

#### Scenario: Mid-run failure

- **WHEN** the contestant process crashes after 2K capture begins
- **THEN** the evaluator catches the failure via its trap, analyzes whatever screenshots were captured (counting missing or unrecognized frames as failures), still produces a `score.json` and `report.html` reflecting partial data, terminates MediaMTX, and the same script's cleanup trap kills the contestant process group, tears down the contestant bandwidth-shaping state, frees ports, releases the flock, and exits

#### Scenario: MediaMTX is owned by this script

- **WHEN** the script starts a run
- **THEN** it brings MediaMTX up and tears it down within its own lifetime; it MUST NOT assume a pre-existing host-resident MediaMTX

#### Scenario: Bandwidth limiter is torn down on every exit path

- **WHEN** a run ends for any reason after the bandwidth limit was applied
- **THEN** the cleanup trap idempotently removes the loopback `tc` qdisc and nftables marking rules, so a later evaluator invocation can apply a fresh limit without inheriting stale shaping state
