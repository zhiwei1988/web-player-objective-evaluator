## MODIFIED Requirements

### Requirement: Scoring

`scorer.py` SHALL accept one or more `--metrics PROFILE=PATH` arguments (one per profile, e.g. `--metrics 2k=results/.../2k_metrics.json --metrics 4k=results/.../4k_metrics.json`), `--output`, and `--report`; score 2K for up to 10 points (5 correctness + 5 FPS), score 4K for up to 15 points (5 correctness + 10 FPS), compute an additional 0-5 point CPU sub-score based on contestant CPU usage measured during the **2K profile** round (see the Contestant CPU Usage Measurement requirement); and produce a `score.json` containing one block per profile (keys `2k`, `4k`), a top-level `cpu` block, a top-level `gate` block, `objective_total`, and `max_score` of `30`.

**Decode-path gate** (first, per profile, UNCHANGED): when the profile's `metrics.decode_forensics.verdict == "violation"`, that profile's `correctness_points` AND `fps_points` SHALL both be `0` regardless of measured rates or FPS, and the profile block SHALL include a `decode_path` sub-block (`verdict`, `checks`, `evidence`). When the violating profile is `2k`, the CPU block SHALL additionally be set to `points=0, gated=true, gate_reason="decode_path_violation"`. A verdict of `ok`, `inconclusive`, or an absent `decode_forensics` block SHALL NOT affect scoring (fail-open); an `inconclusive` verdict on any profile SHALL set top-level `score.json.review_required = true`.

**Correctness** (per profile): full 5 when `watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`; partial 2 when `watermark_recognition_rate >= 0.80` AND `mean_ssim >= 0.75`; otherwise 0. This applies to both 2K and 4K. 4K correctness SHALL remain scored even when 4K FPS is low — EXCEPT when that profile's decode-path verdict is `violation`.

**2K FPS** (threshold based): expected FPS SHALL come from `PROFILES["2k"].fps` and default to `20`. Full 5 when `measured_fps / expected_fps >= FPS_FULL_RATIO_BY_PROFILE["2k"]`; partial 3 at/above `FPS_PARTIAL_RATIO_BY_PROFILE["2k"]`; otherwise 0. Defaults `0.85` / `0.50`. The 2K block SHALL record `fps_full_threshold_used`, `fps_partial_threshold_used`, and `expected_fps`.

**4K FPS** (linear absolute): expected FPS SHALL come from `PROFILES["4k"].fps` and default to `20`. `score.json.4k.fps_points` SHALL equal `round(min(max(measured_fps, 0) / expected_fps, 1.0) * 10, 2)`. The 4K block SHALL record `fps_scoring_mode = "linear_absolute"`, `fps_linear_full_score = 10`, and `expected_fps`.

**CPU**: `scorer.score_cpu(...)` SHALL return an integer in `[0, 5]` with a nullable `gate_reason`, computed exactly as before (fps floor → `sampler_no_data` → full/zero/partial bands). The CPU block SHALL record `measured_on_profile = "2k"`, `gate_profile = "2k"`, `expected_fps`, `measured_fps`, `thresholds_used`, `mean_percent`, `sample_count`, and the existing audit fields.

**Level-0 gate conditions the total** (see the Level-0 Gate (Decode Correctness) requirement). The gate passes iff `2k.correctness_points == 5` AND `4k.correctness_points == 5`.

- **When the gate PASSES**, each profile's `total` SHALL equal `correctness_points + fps_points`, the CPU sub-score SHALL be scored normally, and `objective_total` SHALL equal `2k.total + 4k.total + cpu.points` (the full 30-point scheme).
- **When the gate FAILS**, level-1 SHALL NOT contribute: each profile's `total` SHALL equal its `correctness_points` alone (FPS still computed and recorded under `fps_points`, but excluded from `total`), the CPU block SHALL be set to `points=0, gated=true, gate_reason="gate_failed"` (UNLESS a more specific reason already applies — `decode_path_violation`, `2k_fps_below_threshold`, `sampler_no_data`, `2k_round_failed`, `container_mode_unsupported`, `host_failure` — which is retained), and `objective_total` SHALL equal `2k.correctness_points + 4k.correctness_points`.

`max_score` SHALL remain `30`. Because reaching `30` requires both full correctness (gate pass) and full level-1 performance, decode correctness alone caps the total at `≤ 10` ("level-0 has no full marks"). `objective_total` MAY be fractional (4K FPS is fractional) and SHALL be rounded consistently for display. No profile block SHALL carry `reason = "skipped_gate_failed"` (both profiles are always captured per the Evaluator Entry Script requirement).

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

- **WHEN** the 4K round's `decode_forensics.verdict == "violation"` (so `4k.correctness_points = 0` and the gate fails)
- **THEN** `objective_total = 2k.correctness_points` (4K contributes 0), level-1 is not scored, and a 2K `violation` (if present) keeps `cpu.gate_reason = "decode_path_violation"` rather than `"gate_failed"`

#### Scenario: OK or inconclusive verdict does not change scoring

- **WHEN** a profile's decode-path verdict is `ok`, `inconclusive`, or absent
- **THEN** that profile is scored on its measured metrics exactly as without forensics; an `inconclusive` verdict additionally sets `score.json.review_required = true`

#### Scenario: Fake-overlay submission

- **WHEN** a submission draws a fake canvas overlay that fails DataMatrix, color block, or SSIM checks
- **THEN** it cannot reach full correctness (5) on that profile, the gate fails, and level-1 is not scored

#### Scenario: Missing profile entry fails loudly

- **WHEN** `scorer.score_fps` is invoked with a profile name absent from `FPS_FULL_RATIO_BY_PROFILE` or `FPS_PARTIAL_RATIO_BY_PROFILE`
- **THEN** `score_fps` raises a clear error (e.g. `KeyError`) rather than silently falling back to a default ratio

## REMOVED Requirements

### Requirement: Level-0 Gate (2K)

**Reason**: Replaced by the **Level-0 Gate (Decode Correctness)** requirement. The gate criterion changes from "2K correctness AND 2K FPS at full marks" to "decode correctness at full marks on **both** profiles"; the gate effect changes from "skip the 4K capture and zero 4K + void CPU" to "unlock level-1 (FPS + CPU) scoring"; and the gate-driven **capture short-circuit** in `scripts/evaluator.sh` (`--gate-check`, gate-first ordering) is removed entirely (both profiles are always captured). The `4k` `reason = "skipped_gate_failed"` value no longer occurs.

**Migration**: Consumers that read the `gate` block must use the new correctness-based criterion fields; `scripts/evaluator.sh` reverts to capturing every profile in `lib/profiles.py::PROFILES` unconditionally; the separate decode-path forensics gate is unchanged.

## ADDED Requirements

### Requirement: Level-0 Gate (Decode Correctness)

Per-profile decode **correctness** SHALL act as a **level-0 gate** that conditions whether the "level-1" performance sub-scores (2K FPS, 4K FPS, and the CPU sub-score) contribute to `objective_total`. The gate is a scoring-stage decision only; it SHALL NOT skip the capture or analysis of any profile.

**Gate criterion.** `scorer.py` SHALL expose `gate_passed(profile_metrics) -> bool`, which returns `True` iff `score_correctness(...) == 5` for the 2K metrics AND `score_correctness(...) == 5` for the 4K metrics — i.e. full decode correctness (`watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`) on BOTH profiles, reusing the existing full-correctness band with NO new tunables. If either profile's metrics are absent or lack the inspected fields, or a decode-path `violation` has zeroed a profile's correctness, the gate SHALL be treated as FAILED.

**Effect of a passing gate.** The level-1 sub-scores SHALL be scored exactly as the Scoring requirement defines: each profile's `total = correctness_points + fps_points`, the CPU sub-score is computed normally, and `objective_total = 2k.total + 4k.total + cpu.points` (up to `30`).

**Effect of a failing gate.** Level-1 SHALL NOT contribute:

- each profile's `total` SHALL equal its `correctness_points` alone (its `fps_points` is still computed and recorded for audit, but excluded from `total` and from `objective_total`);
- the CPU block SHALL be set to `points = 0`, `gated = true`, `gate_reason = "gate_failed"` — UNLESS a more specific reason already applies (`decode_path_violation`, `2k_fps_below_threshold`, `sampler_no_data`, `2k_round_failed`, `container_mode_unsupported`, `host_failure`), which is retained;
- `objective_total` SHALL equal `2k.correctness_points + 4k.correctness_points`.

**Single source of the gate decision.** `build_score` SHALL derive the gate verdict from the supplied per-profile metrics and enforce the effects above regardless of caller, so a manual `scorer.py --metrics 2k=… --metrics 4k=…` run is self-consistent. `score.json` SHALL include a top-level `gate` block recording at least `{ "passed": <bool>, "2k_correctness_points": <int>, "4k_correctness_points": <int> }`.

**No capture short-circuit.** Both profiles SHALL always be captured and analyzed (the gate requires 4K correctness). `scorer.py` SHALL NOT provide a `--gate-check` capture-short-circuit mode, and `scripts/evaluator.sh` SHALL NOT gate downstream capture on the level-0 verdict.

#### Scenario: Full correctness on both profiles opens the gate

- **WHEN** `2k.correctness_points == 5` AND `4k.correctness_points == 5`
- **THEN** `score.json.gate.passed = true`, the 2K/4K FPS and CPU sub-scores are scored, and `objective_total = 2k.total + 4k.total + cpu.points`

#### Scenario: Sub-full correctness on either profile closes the gate

- **WHEN** `2k.correctness_points == 5` but `4k.correctness_points < 5` (or vice versa)
- **THEN** `score.json.gate.passed = false`, each profile's `total` equals its correctness only, `cpu.points = 0` with `cpu.gate_reason = "gate_failed"`, and `objective_total = 2k.correctness_points + 4k.correctness_points`

#### Scenario: FPS does not affect the gate

- **WHEN** both profiles reach full correctness but 2K FPS and 4K FPS are below their full-credit bands
- **THEN** `score.json.gate.passed = true` (FPS is not part of the criterion); the FPS points are scored on their own bands and added to the total

#### Scenario: Missing profile metrics fail the gate

- **WHEN** no `4k_metrics.json` was produced (the 4K round failed entirely)
- **THEN** `4k.correctness_points` is treated as `0`, `score.json.gate.passed = false`, level-1 is not scored, and `objective_total` reflects only the 2K correctness

#### Scenario: No --gate-check short-circuit exists

- **WHEN** `scripts/evaluator.sh` runs a submission
- **THEN** both the `2k` and `4k` rounds are captured and analyzed regardless of the 2K result, and no `scorer.py --gate-check` invocation drives capture skipping
