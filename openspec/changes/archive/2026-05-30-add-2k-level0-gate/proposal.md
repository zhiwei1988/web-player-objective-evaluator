## Why

The 2K H.265 round is the baseline competency check: a submission that cannot
decode 2K correctly and keep up frame-rate has no business being measured on the
heavier 4K round or on CPU efficiency. Today every profile is captured and scored
unconditionally, so a submission that fails 2K still pays ~the wall-clock cost of
the 4K capture and can accumulate 4K/CPU points that misrepresent a fundamentally
broken player. We want 2K decode-correctness and 2K frame-rate to act as a
**level-0 gate**: only a perfect 2K unlocks the rest.

## What Changes

- Introduce a **level-0 gate**: the 2K `correctness` sub-score AND the 2K `fps`
  sub-score must BOTH be at full marks (5 and 5) for the run to proceed to the
  4K round and the CPU sub-score.
- Gate criterion reuses the existing full-mark thresholds (no new tunables):
  `correctness == 5` (watermark ≥ 0.95, color ≥ 0.95, SSIM ≥ 0.90) AND
  `fps == 5` (measured/expected ≥ 0.85).
- When the gate fails: the 2K sub-scores are **kept at their actual values**
  (0–8 combined); the 4K round is **not captured**; and 4K and CPU are scored 0.
  `objective_total` therefore equals the 2K actual points only.
- `scorer.py` becomes the single source of the gate decision: a `gate_passed()`
  helper, a `--gate-check` CLI mode (exit 0/1) that `scripts/evaluator.sh` queries
  after the 2K round to short-circuit the 4K capture, and gate enforcement inside
  `build_score` so the final score is gate-consistent regardless of caller.
- `scripts/evaluator.sh` runs the gate profile first (explicitly, not by
  alphabetical accident) and skips the remaining profiles' capture when the gate
  fails. The short-circuit is a pure time optimization — it cannot change the
  score, which `build_score` derives independently.
- `score.json` gains contestant-visible gate signals: the skipped 4K block carries
  `reason: "skipped_gate_failed"`, the CPU block is gated with
  `gate_reason: "gate_failed"`, and a top-level `gate` block records the verdict.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `evaluator`: the Scoring requirement gains a level-0 gate that conditions the 4K
  round and CPU sub-score on a perfect 2K; the Evaluator Entry Script requirement
  gains the gate-driven short-circuit of downstream capture.

## Impact

- **Code**: `scorer.py` (gate helper, `--gate-check` mode, gate enforcement in
  `build_score`, gate block in `score.json`), `scripts/evaluator.sh` (gate-first
  ordering + capture short-circuit), `report.py` (render the gate verdict and
  skipped/ gated reasons).
- **Spec**: `openspec/specs/evaluator/spec.md` — Scoring and Evaluator Entry Script
  requirements.
- **Tests**: existing fixtures whose 2K is intentionally non-perfect will see 4K
  and CPU drop to 0; assertions on those combined totals must be updated. The
  reference submission (perfect 2K) passes the gate and is unaffected.
- **Contestant-facing**: `score.json` shape additions only (new `gate` block, new
  `reason`/`gate_reason` strings); no change to the runtime contract, URLs, env
  vars, or DOM signals.
