## Context

The objective scorer awards up to 5 FPS points per profile via `scorer.score_fps(measured, expected)`. The current band is uniform across profiles: full at `ratio >= 0.50`, partial at `ratio >= 0.25`, zero below. The threshold pair is duplicated in the `evaluator` spec's Scoring requirement.

Operationally this has proven too lenient. A test where 4K hit 17.29 fps out of 25 fps (69.2%) still received full credit, even though 31% of expected frames were missing — well past what a real-time player should drop on a contest-grade host. 2K is significantly less demanding (smaller frames, lower bitrate) so the same percentage of dropped frames there represents a worse implementation than at 4K.

This change introduces per-profile FPS full-credit thresholds while leaving the partial-credit and CPU-gate semantics intact.

## Goals / Non-Goals

**Goals:**
- Different full-credit thresholds for 2K vs 4K, tunable as a single line in `scorer.py`.
- Audit transparency: every `score.json` per-profile block records the threshold actually applied.
- Minimal blast radius: only `score_fps` and the Scoring spec change.

**Non-Goals:**
- Per-profile correctness or SSIM tuning.
- Making `CPU_GATE_FPS_RATIO` derive automatically from `FPS_FULL_RATIO_BY_PROFILE["4k"]`. The two coincide today (both `0.65`) but stay as independent module constants so a future tightening of one does not silently move the other.

## Decisions

### Decision 1: Per-profile thresholds live in `scorer.py`, not in `PROFILES`

**Choice:** Add two module-level dicts in `scorer.py`:
```python
FPS_FULL_RATIO_BY_PROFILE:    dict[str, float] = {"2k": 0.85, "4k": 0.65}
FPS_PARTIAL_RATIO_BY_PROFILE: dict[str, float] = {"2k": 0.50, "4k": 0.40}
```

**Alternative considered:** Add `fps_full_ratio` / `fps_partial_ratio` fields to `lib/profiles.ProfileSpec`.

**Rationale:** CLAUDE.md's "Source of truth" table assigns scoring tunables to `scorer.py` module-level constants, and `PROFILES` to per-round capture parameters (resolution, bitrate, RTSP path, …). Both ratios are scoring policy decisions, not properties of the stream. Keeping them in `scorer.py` mirrors the established `CPU_FULL_THRESHOLD_PERCENT` / `CPU_ZERO_THRESHOLD_PERCENT` pattern and keeps `PROFILES` lean.

**Invariant:** For every profile, `partial < full` SHALL hold; this is asserted at module-import time so a typo can't silently make the partial band empty.

### Decision 2: `score_fps` gains a `profile: str` parameter

**Choice:**
```python
def score_fps(measured: float, expected: float, profile: str) -> int: ...
```

**Alternative considered:** Pass the threshold ratio directly.

**Rationale:** Callers already know the profile (`score_profile(profile, metrics)` is the only production caller). Passing the profile keeps the threshold lookup centralized in `score_fps` and lets the function record what it applied without callers having to thread state. The function still raises `KeyError` for unknown profiles, surfacing typos loudly.

### Decision 3: Audit fields on the per-profile block, not on `cpu.thresholds_used`

**Choice:** Each profile block in `score.json` gains both `fps_full_threshold_used: float` and `fps_partial_threshold_used: float`. The existing `cpu.thresholds_used` block is unchanged.

**Alternative considered:** Add the FPS thresholds to `cpu.thresholds_used`, or collapse to a single nested object `fps_thresholds_used: {full, partial}`.

**Rationale:** `cpu.thresholds_used` is documented as the audit trail for the CPU sub-score only — mixing FPS thresholds in there would confuse the schema. Two flat fields on the profile block are self-describing, mirror `measured_fps` / `expected_fps` already present there, and are easy to consume in `report.py` without a nested traversal.

### Decision 4: Backward-compatibility / no fallbacks

`score_fps` raises `KeyError` if a profile is missing from either `FPS_FULL_RATIO_BY_PROFILE` or `FPS_PARTIAL_RATIO_BY_PROFILE`. We do NOT add a default fallback ratio — that would silently re-introduce the old behavior if a new profile is added without a scoring-policy decision. Adding a profile MUST come with deliberate entries in both dicts. This is consistent with the project's "no silent fallback" stance for MediaMTX port binding and source-built dependencies.

### Decision 5: Raise `CPU_GATE_FPS_RATIO` from `0.25` to `0.65`

**Choice:** Move the CPU gate to the same ratio as the 4K full-credit threshold. The gate semantic shifts from "did 4K play at all" to "did 4K reach full FPS credit." A submission that only earns partial 4K FPS points now also forfeits the CPU sub-score.

**Alternative considered:** Keep the gate at `0.25` and treat CPU efficiency as orthogonal to playback quality.

**Rationale:** CPU efficiency is only meaningful when the player is actually keeping up. A contestant who drops 40 % of 4K frames may show low CPU usage because the renderer is idling — that's not efficiency, it's failure to draw. Coupling the gate to full-credit eligibility prevents that score-laundering. The module constant stays independent of `FPS_FULL_RATIO_BY_PROFILE["4k"]` (see Non-Goals) so the two can drift apart deliberately if a future change requires it.

**Side-effect to update:** The current `scorer.py` docstring on `CPU_GATE_FPS_RATIO` says "Default mirrors score_fps's partial-credit threshold." That sentence becomes stale and must be rewritten to reference the 4K full-credit alignment instead.

## Risks / Trade-offs

- **Risk: Contestant appeal on tightened bar.** → Mitigation: The audit field surfaces the exact threshold used; the spec documents the per-profile ratio so an appeal can be answered with the rule, not a debate.
- **Risk: Adding a future profile without updating the dicts crashes scoring.** → Mitigation: This is by design (Decision 4). A test asserting both `set(FPS_FULL_RATIO_BY_PROFILE) == set(PROFILES)` and `set(FPS_PARTIAL_RATIO_BY_PROFILE) == set(PROFILES)` catches it at CI time.
- **Trade-off: Partial band narrows compared to the original `0.25` floor.** A 4K submission rendering at 7 fps (`ratio = 0.28`) used to score 3 partial points; under the new `0.40` 4K partial floor it scores 0. That's intentional — partial credit is "playing but degraded"; sub-`0.40` 4K is closer to "not really playing." The corresponding numbers for 2K are 12.5 fps (new floor) vs 6.25 fps (old).
- **Trade-off: CPU sub-score dependency on 4K full credit.** Raising `CPU_GATE_FPS_RATIO` to `0.65` means a submission scoring 3 FPS points on 4K (partial band) also loses the CPU sub-score regardless of its measured CPU percent. That can swing the total by up to 10 points. This is intentional per Decision 5 — but the report should make the gate reason prominent so contestants understand why CPU went to 0.

## Migration Plan

No data migration needed. The scoring change applies to every new run after the code lands. Existing `score.json` files in `results/` are not rewritten. Tests under `tests/` need updates wherever they assert FPS scoring outcomes.

## Open Questions

None — thresholds, fallback policy, and audit field placement are decided above.
