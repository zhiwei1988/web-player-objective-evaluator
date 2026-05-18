## Why

The current FPS scoring band awards full 5 points whenever `measured_fps / expected_fps >= 0.50` for both profiles, which lets contestants who only render at half the target rate (12.5 fps against 25 fps expected) collect the same FPS score as contestants hitting near-spec playback. A recent test run produced 4K at 17.29 fps / 25 fps (69.2%) and still received full credit — well below what the contest considers acceptable real-time playback for a high-resolution stream. The full-credit threshold must be tightened, and 2K (a much easier profile) must be held to a stricter bar than 4K.

## What Changes

- **BREAKING** (for contestant scoring): Replace the single `score_fps` full-credit ratio `0.50` with per-profile thresholds.
  - 2K: full 5 when `measured_fps / expected_fps >= 0.85`
  - 4K: full 5 when `measured_fps / expected_fps >= 0.65`
- **BREAKING** (for contestant scoring): Replace the single partial-credit ratio `0.25` with per-profile thresholds.
  - 2K: partial 3 when `measured_fps / expected_fps >= 0.50` (otherwise 0)
  - 4K: partial 3 when `measured_fps / expected_fps >= 0.40` (otherwise 0)
- **BREAKING** (for contestant scoring): Raise `CPU_GATE_FPS_RATIO` from `0.25` to `0.65`. The CPU sub-score now requires the 4K round to reach at least 65 % of expected FPS; this aligns the CPU gate with the 4K full-credit bar, so a submission that does not earn full 4K FPS credit cannot earn any CPU points.
- Introduce `FPS_FULL_RATIO_BY_PROFILE: dict[str, float]` and `FPS_PARTIAL_RATIO_BY_PROFILE: dict[str, float]` as module-level constants in `scorer.py`. `score_fps` gains a `profile` parameter that resolves both thresholds from these dicts.
- `score.json` per-profile blocks gain `fps_full_threshold_used` and `fps_partial_threshold_used` fields so an audit can see exactly which ratios produced the score (parallel to the existing `cpu.thresholds_used` pattern).

## Capabilities

### New Capabilities

(none — purely a tightening of existing scoring rules)

### Modified Capabilities

- `evaluator`: Scoring requirement's FPS band is reworded to be per-profile; `score.json` schema gains the per-profile audit field.

## Impact

- **Affected code**: `scorer.py` (module constants including `CPU_GATE_FPS_RATIO`, `score_fps`, `ProfileScore`, `score_profile`, `_build_cpu_block` thresholds-used echo), tests under `tests/` for any `score_fps` / `score_cpu` assertions, `report.py` if it displays the threshold.
- **Affected artifacts**: Every future `score.json` will carry the two new per-profile audit fields and the new `cpu.thresholds_used.gate_fps_ratio = 0.65`; existing archived `score.json` files are not rewritten.
- **Contestant-visible impact**: Stricter scoring. Re-running the most recent test (2K 88.8 %, 4K 69.2 %) keeps both FPS scores at 5 and CPU at 10. The new thresholds catch submissions worse than this baseline that the old `0.50` / `0.25` rule did not: 2K loses all FPS points below 12.5 fps (was 6.25), 4K loses all FPS points below 10 fps (was 6.25), and any 4K below 16.25 fps now also forfeits the CPU sub-score.
- **No dependency, no API, no config-file changes.**
