## 1. Failing Tests

- [x] 1.1 Add unit tests in `tests/test_score_fps.py` covering the per-profile full-credit cutoff at exactly the boundary, just above, and just below for both `2k` (0.85) and `4k` (0.65)
- [x] 1.2 Add unit tests for the per-profile partial-credit cutoff at exactly the boundary, just above, and just below for both `2k` (0.50) and `4k` (0.40)
- [x] 1.3 Add a unit test that `score_fps` raises a clear error (e.g. `KeyError`) when called with a profile absent from either `FPS_FULL_RATIO_BY_PROFILE` or `FPS_PARTIAL_RATIO_BY_PROFILE`
- [x] 1.4 Add a guard test asserting `set(scorer.FPS_FULL_RATIO_BY_PROFILE) == set(scorer.FPS_PARTIAL_RATIO_BY_PROFILE) == set(lib.profiles.PROFILES)` so adding a profile without scoring ratios fails at CI time
- [x] 1.5 Add a guard test asserting `scorer.FPS_PARTIAL_RATIO_BY_PROFILE[p] < scorer.FPS_FULL_RATIO_BY_PROFILE[p]` for every profile
- [x] 1.6 Add a unit test on `build_score` (or its callers) asserting each per-profile block in the returned dict includes both `fps_full_threshold_used` and `fps_partial_threshold_used` equal to the ratios applied to that profile
- [x] 1.7 Extend `tests/test_score_cpu.py` (or add cases) covering: gate at the new `0.65` boundary (just above passes through to the cpu scorer, just below trips `"4k_fps_below_threshold"`); a "4K in partial-FPS band (e.g. 0.50 ratio, 3 fps points) but CPU is gated to 0" scenario
- [x] 1.8 Update any existing CPU test that hard-codes `gate_fps_ratio=0.25` so it either pins the value explicitly via the parameter (when verifying behavior at the historical default) or moves to the new `0.65` default — pick whichever is correct for the test's intent
- [x] 1.9 Run the new and updated tests; confirm they fail against the current implementation

## 2. Implementation

- [x] 2.1 In `scorer.py`, introduce both `FPS_FULL_RATIO_BY_PROFILE: dict[str, float] = {"2k": 0.85, "4k": 0.65}` and `FPS_PARTIAL_RATIO_BY_PROFILE: dict[str, float] = {"2k": 0.50, "4k": 0.40}` as module-level constants with docstrings referencing the spec
- [x] 2.2 Add a module-level invariant check (e.g. an assertion block executed at import time) that for every profile in `FPS_FULL_RATIO_BY_PROFILE`, `FPS_PARTIAL_RATIO_BY_PROFILE[profile] < FPS_FULL_RATIO_BY_PROFILE[profile]`; failure must name the offending profile
- [x] 2.3 Change `score_fps(measured, expected)` to `score_fps(measured, expected, profile)`; resolve both ratios from the dicts by profile
- [x] 2.4 Update `ProfileScore` dataclass to carry `fps_full_threshold_used: float` and `fps_partial_threshold_used: float`; thread both through `to_dict()` so they land in `score.json`
- [x] 2.5 Update `score_profile(profile, metrics)` to populate the new fields and pass `profile` to `score_fps`
- [x] 2.6 Confirm no production caller of `score_fps` lost a parameter; update any in-repo references (CLI, report) if needed
- [x] 2.7 Change `scorer.CPU_GATE_FPS_RATIO` default from `0.25` to `0.65`; rewrite its docstring to note alignment with `FPS_FULL_RATIO_BY_PROFILE["4k"]` and remove the stale "mirrors score_fps's partial-credit threshold" line
- [x] 2.8 Confirm `_build_cpu_block` and `_thresholds_used` still echo the active `CPU_GATE_FPS_RATIO` into `score.json.cpu.thresholds_used.gate_fps_ratio` (no code change expected, just verify)
- [x] 2.9 Run the tests from §1; they MUST now pass

## 3. Report And Audit Surface

- [x] 3.1 In `report.py`, display each profile's `fps_full_threshold_used` and `fps_partial_threshold_used` alongside the existing measured/expected FPS line so the report explains why a given submission got 5, 3, or 0
- [x] 3.2 In `report.py`, when `cpu.gated == true` and `cpu.gate_reason == "4k_fps_below_threshold"`, surface the new `0.65` gate value prominently so contestants understand why CPU went to 0
- [x] 3.3 If `report.py` snapshots or templates exist in tests, regenerate them or update fixtures

## 4. Regression Verification

- [x] 4.1 Run the full `pytest` suite (or at least `tests/test_score_*`, analyzer, report tests) and confirm no unexpected failures
- [ ] 4.2 Re-run an end-to-end evaluator pass against the most recent test submission (`scripts/evaluator-local.sh`) and confirm the resulting `score.json` shows: `2k.fps_full_threshold_used=0.85`, `2k.fps_partial_threshold_used=0.50`, `4k.fps_full_threshold_used=0.65`, `4k.fps_partial_threshold_used=0.40`, `cpu.thresholds_used.gate_fps_ratio=0.65`, and the per-profile `fps_points` plus `cpu.points` match the new policy
- [x] 4.3 Run `openspec validate tighten-fps-scoring-thresholds --strict`
