## 1. Tests First

- [x] 1.1 Add profile tests asserting both default profiles use `fps=20`, 30s duration, and 2K is the only CPU-sampled profile.
- [x] 1.2 Add scoring tests for 2K threshold FPS at 20fps expected values.
- [x] 1.3 Add scoring tests for 4K linear absolute FPS points, including `4fps -> 1.0`, cap at 5, and fractional objective totals.
- [x] 1.4 Add CPU scoring tests proving the CPU gate uses 2K metrics, reports `measured_on_profile="2k"`, emits `2k_fps_below_threshold`, and handles missing `2k_metrics.json` as `2k_round_failed`.
- [x] 1.5 Add runner/analyzer tests proving CPU metadata is written and passed through for 2K, and omitted for 4K.
- [x] 1.6 Add report tests proving the report shows CPU measured on 2K and the 4K linear FPS formula.

## 2. Profile And Stream Generation

- [x] 2.1 Update `lib/profiles.py` so 2K and 4K default to 20fps and only 2K has `cpu_sampled=True`.
- [x] 2.2 Update stream generation expectations so GOP and frame counts derive from profile FPS and default to 600 frames per profile.
- [x] 2.3 Ensure `scripts/prepare_streams.sh` regenerates stale 25fps outputs and removes any incompatible old references.

## 3. Capture And CPU Measurement

- [x] 3.1 Update `scripts/evaluator.sh` to pass `--contestant-pgid` only for profiles whose `ProfileSpec.cpu_sampled` is true, with 2K as the default sampled profile.
- [x] 3.2 Update `runner.py` CPU metadata expectations so `capture_meta.json` is produced for 2K and not for 4K.
- [x] 3.3 Update `analyzer.py` CPU passthrough so CPU data from `2k_screenshots/capture_meta.json` lands in `2k_metrics.json`.

## 4. Scoring And Reporting

- [x] 4.1 Update `scorer.py` so expected FPS comes from each profile's `ProfileSpec.fps`.
- [x] 4.2 Implement 4K linear absolute FPS scoring with audit fields `fps_scoring_mode`, `fps_linear_full_score`, and `expected_fps`.
- [x] 4.3 Update CPU scoring to read the sampled profile from 2K metrics, gate on 2K measured FPS, and record `measured_on_profile`, `gate_profile`, measured FPS, expected FPS, and thresholds used.
- [x] 4.4 Update failure-score paths so CPU gate reasons use `2k_round_failed`, `2k_fps_below_threshold`, `sampler_no_data`, or `host_failure` as appropriate.
- [x] 4.5 Update `report.py` to render 20fps expected values, the 4K linear FPS formula, fractional FPS points, and 2K CPU measurement details.

## 5. Documentation And Validation

- [x] 5.1 Update README scoring sections and lifecycle notes to document 20fps streams, 2K CPU scoring, and 4K linear FPS scoring.
- [x] 5.2 Run focused unit tests for profiles, scoring, analyzer CPU passthrough, runner CPU behavior, and report generation.
- [x] 5.3 Run `scripts/build_test_zips.sh` and `scripts/test.sh` after regenerating streams to validate the full evaluator flow.
- [x] 5.4 Run `openspec status --change adapt-scoring-for-constrained-host` and confirm the change is apply-ready.
