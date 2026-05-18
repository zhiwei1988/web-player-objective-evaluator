## MODIFIED Requirements

### Requirement: Scoring

`scorer.py` SHALL accept one or more `--metrics PROFILE=PATH` arguments (one per profile, e.g. `--metrics 2k=results/.../2k_metrics.json --metrics 4k=results/.../4k_metrics.json`), `--output`, and `--report`; score each profile independently for up to 10 points (5 correctness + 5 FPS); compute an additional 0–10 point CPU sub-score based on contestant CPU usage measured during the **4K profile** round (see the Contestant CPU Usage Measurement requirement); and produce a `score.json` containing one block per profile (keys `2k`, `4k`), a top-level `cpu` block, `objective_total`, and `max_score` of `30`.

**Correctness** (rescaled): full 5 when `watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`; partial 2 when `watermark_recognition_rate >= 0.80` AND `mean_ssim >= 0.75`; otherwise 0.

**FPS** (per-profile full- and partial-credit ratios): expected `25` for both profiles. Full 5 when `measured_fps / expected_fps >= FPS_FULL_RATIO_BY_PROFILE[profile]`; partial 3 when `measured_fps / expected_fps >= FPS_PARTIAL_RATIO_BY_PROFILE[profile]`; otherwise 0. Both are module-level `dict[str, float]` in `scorer.py` with default values `FPS_FULL_RATIO_BY_PROFILE = {"2k": 0.85, "4k": 0.65}` and `FPS_PARTIAL_RATIO_BY_PROFILE = {"2k": 0.50, "4k": 0.40}`. Each dict SHALL contain an entry for every key in `lib/profiles.py::PROFILES`, and for every profile the partial ratio SHALL be strictly less than the full ratio; scoring SHALL fail loudly if a profile is missing or the invariant is violated (no silent fallback). Each per-profile block in `score.json` SHALL include `fps_full_threshold_used` and `fps_partial_threshold_used` recording the ratios applied to that profile so an audit can verify the score.

**CPU** (gating on 4K): `scorer.score_cpu(mean_cpu_percent, measured_4k_fps, expected_4k_fps)` SHALL return an integer in `[0, 10]` together with a nullable `gate_reason` string, evaluated in this order:

1. Gate: if `measured_4k_fps / expected_4k_fps < CPU_GATE_FPS_RATIO` (default `0.65`, aligned with `FPS_FULL_RATIO_BY_PROFILE["4k"]`), return `(0, "4k_fps_below_threshold")`. A submission that does not reach full 4K FPS credit cannot earn any CPU points. The two ratios are independent module constants and MAY drift apart deliberately in a future change.
2. If `mean_cpu_percent` is unavailable (analyzer omitted the field, or `capture_meta.json.cpu` was null), return `(0, "sampler_no_data")`.
3. If `mean_cpu_percent <= CPU_FULL_THRESHOLD_PERCENT` (default `5.0`), return `(10, None)`.
4. If `mean_cpu_percent > CPU_ZERO_THRESHOLD_PERCENT` (default `20.0`), return `(0, None)`.
5. Otherwise return `(round((CPU_ZERO_THRESHOLD_PERCENT - mean_cpu_percent) / (CPU_ZERO_THRESHOLD_PERCENT - CPU_PARTIAL_START_PERCENT) * 10), None)`, clamped to `[0, 10]`. `CPU_PARTIAL_START_PERCENT` defaults to `6.0`.

The six tunables (`CPU_GATE_FPS_RATIO`, `CPU_FULL_THRESHOLD_PERCENT`, `CPU_PARTIAL_START_PERCENT`, `CPU_ZERO_THRESHOLD_PERCENT`, `CPU_MIN_SAMPLES`, `_cpu_sampler.DEFAULT_SAMPLE_HZ`) SHALL be exposed as named module-level constants and the actual values applied to each run SHALL be recorded under `score.json.cpu.thresholds_used` so a contestant audit can verify which thresholds produced the score.

When the 4K round fails entirely (no `4k_metrics.json` produced, or it lacks the fields the gate inspects), `scorer.py` SHALL still emit a complete `score.json` with `cpu.points=0`, `cpu.gated=true`, `cpu.gate_reason="4k_round_failed"`, and `cpu.mean_percent=null`. When the host-side wrapper fails before the evaluator main body runs, the existing failure `score.json` path SHALL include `cpu.gated=true`, `cpu.gate_reason="host_failure"`, `cpu.points=0`.

`objective_total` SHALL equal `2k.total + 4k.total + cpu.points` and SHALL NOT exceed `max_score` (`30`).

#### Scenario: Per-profile totals

- **WHEN** scoring completes for a submission
- **THEN** `score.json` includes a `2k` block, a `4k` block, a top-level `cpu` block, an `objective_total` equal to `2k.total + 4k.total + cpu.points`, and `max_score = 30`, with the underlying metrics (rates, mean SSIM, measured FPS, CPU mean percent, sample count, thresholds applied) preserved for audit; the keys `h264` and `h265` SHALL NOT appear

#### Scenario: Static-frame submission

- **WHEN** a submission renders a single static image so `measured_fps` is near 0 for both profiles
- **THEN** each profile receives 0 FPS points; correctness is scored on its own merits; the CPU gate trips on `measured_4k_fps / expected_4k_fps < CPU_GATE_FPS_RATIO` so `cpu.points=0` and `cpu.gate_reason="4k_fps_below_threshold"`

#### Scenario: Fake-overlay submission

- **WHEN** a submission draws a fake canvas overlay that visually resembles the watermark but fails DataMatrix, color block, or SSIM checks
- **THEN** it cannot reach full correctness (5) per profile and may receive 2 or 0 depending on which checks it passes

#### Scenario: 2K FPS full credit requires 85% of expected

- **WHEN** a submission's 2K round produces `measured_fps / expected_fps >= 0.85` (e.g. `22.20` fps against expected `25` fps = `88.8%`)
- **THEN** `score.json.2k.fps_points = 5`, `score.json.2k.fps_full_threshold_used = 0.85`, and `score.json.2k.fps_partial_threshold_used = 0.50`

#### Scenario: 2K FPS in the partial band receives 3 points

- **WHEN** a submission's 2K round produces `0.50 <= measured_fps / expected_fps < 0.85` (e.g. `15` fps / `25` fps = `60%`)
- **THEN** `score.json.2k.fps_points = 3`, `score.json.2k.fps_full_threshold_used = 0.85`, and `score.json.2k.fps_partial_threshold_used = 0.50`

#### Scenario: 2K FPS below the partial floor receives 0

- **WHEN** a submission's 2K round produces `measured_fps / expected_fps < 0.50` (e.g. `10` fps / `25` fps = `40%`)
- **THEN** `score.json.2k.fps_points = 0` and `score.json.2k.fps_partial_threshold_used = 0.50`

#### Scenario: 4K FPS full credit requires 65% of expected

- **WHEN** a submission's 4K round produces `measured_fps / expected_fps >= 0.65` (e.g. `17.29` fps against expected `25` fps = `69.2%`)
- **THEN** `score.json.4k.fps_points = 5`, `score.json.4k.fps_full_threshold_used = 0.65`, and `score.json.4k.fps_partial_threshold_used = 0.40`

#### Scenario: 4K FPS in the partial band receives 3 points

- **WHEN** a submission's 4K round produces `0.40 <= measured_fps / expected_fps < 0.65` (e.g. `12` fps / `25` fps = `48%`)
- **THEN** `score.json.4k.fps_points = 3`, `score.json.4k.fps_full_threshold_used = 0.65`, and `score.json.4k.fps_partial_threshold_used = 0.40`

#### Scenario: 4K FPS below the partial floor receives 0

- **WHEN** a submission's 4K round produces `measured_fps / expected_fps < 0.40` (e.g. `8` fps / `25` fps = `32%`)
- **THEN** `score.json.4k.fps_points = 0` and `score.json.4k.fps_partial_threshold_used = 0.40`

#### Scenario: CPU gate trips when 4K does not earn full FPS credit

- **WHEN** the 4K round produces `measured_fps / expected_fps = 0.50` (above the 0.40 4K partial-FPS bar, below the 0.65 CPU gate)
- **THEN** `score.json.4k.fps_points = 3` AND `score.json.cpu.points = 0` AND `score.json.cpu.gated = true` AND `score.json.cpu.gate_reason = "4k_fps_below_threshold"`, regardless of the measured `mean_cpu_percent`

#### Scenario: Missing profile entry fails loudly

- **WHEN** `scorer.score_fps` is invoked with a profile name absent from `FPS_FULL_RATIO_BY_PROFILE` or `FPS_PARTIAL_RATIO_BY_PROFILE`
- **THEN** `score_fps` raises a clear error (e.g. `KeyError`) rather than silently falling back to a default ratio, ensuring adding a new profile in `PROFILES` cannot bypass a scoring-policy decision

#### Scenario: Partial-below-full invariant is enforced at import time

- **WHEN** `scorer.py` is imported with a profile whose `FPS_PARTIAL_RATIO_BY_PROFILE[profile] >= FPS_FULL_RATIO_BY_PROFILE[profile]`
- **THEN** import fails with a clear assertion-style error naming the offending profile, so a configuration typo cannot collapse the partial band silently

#### Scenario: CPU full marks at low usage

- **WHEN** the 4K round produces `measured_fps / expected_fps >= CPU_GATE_FPS_RATIO` (passes the gate) AND `mean_cpu_percent <= 5.0`
- **THEN** `cpu.points = 10`, `cpu.gated = false`, `cpu.gate_reason = null`, and `cpu.thresholds_used` records every threshold that produced this outcome

#### Scenario: CPU partial credit in the proportional band

- **WHEN** the 4K round produces `measured_fps / expected_fps >= CPU_GATE_FPS_RATIO` (passes the gate) AND `mean_cpu_percent` falls within `(5.0, 20.0]`
- **THEN** `cpu.points = round((20 - mean_cpu_percent) / 14 * 10)` clamped to `[0, 10]`, `cpu.gated = false`, and the formula matches the published score table at the integer percent breakpoints (`6→10`, `7→9`, `10→7`, `13→5`, `15→4`, `17→2`, `19→1`, `20→0`)

#### Scenario: CPU zero when usage exceeds the upper limit

- **WHEN** the 4K round produces `measured_fps / expected_fps >= CPU_GATE_FPS_RATIO` (passes the gate) AND `mean_cpu_percent > 20.0`
- **THEN** `cpu.points = 0`, `cpu.gated = false`, `cpu.gate_reason = null`, and `cpu.mean_percent` is recorded as-measured for audit (not clipped)

#### Scenario: CPU gated when 4K fps round fails

- **WHEN** the 4K round produces `measured_fps / expected_fps < CPU_GATE_FPS_RATIO` (default `0.65`, i.e. `< 16.25` fps against the expected `25` fps)
- **THEN** `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "4k_fps_below_threshold"`, and `cpu.mean_percent` still records the measured value (or `null` if sampling failed) for diagnostic purposes

#### Scenario: CPU gated when sampler produced no data

- **WHEN** the 4K capture round ran but `capture_meta.json` is missing OR `capture_meta.json.cpu` is null OR fewer than `CPU_MIN_SAMPLES` samples were collected
- **THEN** `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "sampler_no_data"`, and `cpu.mean_percent = null`

#### Scenario: CPU gated when 4K round itself failed

- **WHEN** no `4k_metrics.json` was produced (player error, readiness timeout, missing element, etc.)
- **THEN** `score.json` is still emitted with `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "4k_round_failed"`, and `cpu.mean_percent = null`, alongside whatever `4k.reason` the runner recorded

#### Scenario: Thresholds-used audit field

- **WHEN** any `score.json` is produced
- **THEN** `score.json.cpu.thresholds_used` contains exactly the six keys `gate_fps_ratio`, `full_percent`, `partial_start_percent`, `zero_percent`, `min_samples`, `sample_hz`, each set to the value actually applied to that run, so an organizer can replay the score from the metrics alone
