# Rescope the Level-0 Gate to Decode Correctness

## Why

The only capability worth treating as a hard prerequisite right now is **in-browser
H.265 decode correctness** — can the submission deliver the right picture on *both*
profiles? Frame-rate and CPU efficiency should only be rewarded once a submission has
proven it decodes correctly. Today the level-0 gate (`add-2k-level0-gate`, commit
`d63a30a`) keys on **2K correctness AND 2K FPS**, and it short-circuits the 4K *capture*
when 2K is sub-full — so a submission that decodes both 2K and 4K perfectly but misses
the 2K FPS bar never even gets its 4K correctness measured, and FPS (a performance
metric) acts as a hard gate on the rest. We want the gate to be **decode correctness on
both profiles**, evaluated at scoring time only, with every profile always captured.

## What Changes

- **Redefine the level-0 gate criterion.** The gate passes iff
  `2k.correctness_points == 5` AND `4k.correctness_points == 5` (full decode correctness
  on both profiles). FPS is no longer part of the gate.
- **Redefine the gate effect (scoring-stage only).**
  - **Gate passes** → "level-1" (2K FPS, 4K FPS, CPU) is scored normally and
    `objective_total = 2k.total + 4k.total + cpu.points` (the full 30-point scheme; max `30`).
  - **Gate fails** → level-1 contributes **0**:
    `objective_total = 2k.correctness_points + 4k.correctness_points` (correctness only,
    `≤ 7` since a perfect `5 + 5` would have passed the gate). FPS is still computed and
    shown; CPU is `points = 0, gated = true, gate_reason = "gate_failed"`.
- **`max_score` stays `30`.** Decode correctness alone caps the total well below `30`, so
  full marks require passing the gate **and** earning the level-1 performance points —
  i.e. "level-0 has no full marks" (0级没有满分).
- **Remove the execution short-circuit.** Both `2k` and `4k` are **always** captured and
  analyzed (the gate now needs 4K correctness, so 4K must run); the gate-first ordering
  and `scorer.py --gate-check` short-circuit in `scripts/evaluator.sh` are removed. The
  gate is a pure scoring-stage decision.
- **Decode-path forensics is unchanged.** A `violation` still zeros that profile's
  `correctness` (and `fps`), which necessarily fails the new gate — keeping server-side
  transcoders out of any level-1 credit; an `inconclusive` verdict still sets
  `review_required`.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `evaluator`: the **Scoring** requirement gains a gate-conditioned `objective_total`
  (full 30-point scheme on a pass, correctness-only on a fail); the **Level-0 Gate (2K)**
  requirement is replaced by a **Level-0 Gate (Decode Correctness)** requirement (new
  criterion + effect, no execution short-circuit); the **Evaluator Entry Script**
  requirement drops the gate-driven capture short-circuit (every profile captured
  unconditionally).

## Impact

- **Code**: `scorer.py` (`gate_passed` keys on both profiles' full correctness;
  `build_score` computes the gate-conditioned `objective_total`, zeroing level-1 on a
  fail with `cpu.gate_reason="gate_failed"`; remove the `--gate-check` CLI mode and the
  capture short-circuit it served; keep the `gate` block, updated to the new criterion),
  `scripts/evaluator.sh` (remove gate-first ordering + `--gate-check`; capture every
  profile unconditionally), `report.py` (update the gate banner/summary to the new
  criterion; show level-1 as gated when the gate fails).
- **Spec**: `openspec/specs/evaluator/spec.md` — MODIFY **Scoring** and **Evaluator
  Entry Script**, REMOVE **Level-0 Gate (2K)**, ADD **Level-0 Gate (Decode Correctness)**.
- **Tests**: rewrite `tests/test_gate.py` for the new criterion/effect; update
  `tests/test_report_gate.py` and `tests/test_decode_forensics_scoring.py`; add scorer
  cases — perfect run → `30`; full correctness + zero performance → `10`; partial
  correctness → correctness-only (`≤ 7`) with FPS/CPU zeroed.
- **Contestant-facing**: `score.json` keeps `objective_total` / `max_score = 30` / the
  `gate` block, but the gate block's criterion changes (correctness-based) and the
  `skipped_gate_failed` 4K reason disappears (4K is always captured).
