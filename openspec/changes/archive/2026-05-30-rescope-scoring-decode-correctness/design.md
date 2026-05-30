# Rescope the Level-0 Gate to Decode Correctness - Design

## Context

`add-2k-level0-gate` (commit `d63a30a`) introduced a level-0 gate keyed on the 2K
`correctness` AND 2K `fps` sub-scores: a sub-full 2K caused `scripts/evaluator.sh` to
skip the 4K *capture* (`--gate-check`), and `build_score` zeroed 4K
(`reason="skipped_gate_failed"`) and voided CPU (`gate_reason="gate_failed"`), emitting a
top-level `gate` block. Base scoring is `objective_total = 2k.total + 4k.total +
cpu.points` where each profile `total = correctness_points + fps_points`.

The user has redefined the gate:
- **Criterion** = full decode correctness on **both** profiles
  (`2k.correctness_points == 5` AND `4k.correctness_points == 5`); FPS is no longer in
  the gate.
- **Effect** = the gate unlocks **level-1 scoring** (2K FPS, 4K FPS, CPU), not 4K
  *capture*. Both profiles are always captured (the gate needs 4K correctness). On a
  pass, the full 30-point scheme applies; on a fail, only correctness counts.
- `max_score` stays `30`, so "level-0 has no full marks": correctness alone caps the
  total at `≤ 10`, and reaching `30` requires passing the gate **and** earning level-1.

The decode-path forensics gate is separate and unchanged: a `violation` zeros that
profile's `correctness`/`fps`, which necessarily fails the new gate.

## Goals / Non-Goals

**Goals**
- Gate = both profiles' full correctness; pure scoring-stage decision.
- Gate pass → `objective_total = 2k.total + 4k.total + cpu.points` (max `30`).
- Gate fail → `objective_total = 2k.correctness_points + 4k.correctness_points`, with FPS
  shown but unscored and CPU `points=0, gated, gate_reason="gate_failed"`.
- Both profiles always captured and analyzed (remove the execution short-circuit).
- Single scoring truth source stays in `scorer.py` + `spec.md`; no new tunables.

**Non-Goals**
- No change to how `score_correctness`, `score_fps`, `score_cpu` compute individual
  values — only the gate criterion and what feeds `objective_total`.
- No change to `lib/profiles.py::PROFILES`, the contestant runtime contract, or the
  watermark/analysis pipeline.

## Decisions

### D1: Gate criterion = full correctness on both profiles

`gate_passed(profile_metrics) -> bool` returns
`score_correctness(2k) == 5 and score_correctness(4k) == 5`, reusing the existing
full-correctness band (no new tunables). Missing/incomplete metrics for either profile,
or a decode-path `violation` (which zeros that profile's correctness), fail the gate.
This replaces the old 2K-correctness-AND-2K-FPS criterion. Rationale: the user wants
correct decode on both resolutions to be the prerequisite; FPS is performance, not a
prerequisite.

### D2: `build_score` computes a gate-conditioned `objective_total`

- **Pass**: per-profile `total = correctness_points + fps_points`; CPU scored normally;
  `objective_total = 2k.total + 4k.total + cpu.points` (the original formula).
- **Fail**: per-profile `total = correctness_points` (FPS excluded from the total but
  `fps_points` still recorded for audit); CPU forced to `points=0, gated=true,
  gate_reason="gate_failed"`; `objective_total = 2k.correctness_points +
  4k.correctness_points`.

`build_score` derives the gate from the supplied metrics independently of caller, so a
manual `scorer.py --metrics 2k=… --metrics 4k=…` run is self-consistent.

### D3: `max_score` stays `30`

Keeping the ceiling at `30` makes the gate meaningful: a perfect-correctness but
zero-performance run shows `10/30`; a gate-failed run shows `≤ 7/30`. "Level-0 has no
full marks" is legible directly from `score.json`. (No `max_score=10` variant — the
30-point scheme is fully reachable once the gate opens.)

### D4: Reuse the gate machinery, drop only the execution short-circuit

Keep `gate_passed()`, the top-level `gate` block, and the `gate_failed` CPU reason — but
re-key them on correctness and on unlocking level-1. Remove the
*execution* short-circuit: delete the `--gate-check` CLI mode and the gate-first
ordering from `scripts/evaluator.sh`; revert it to capturing every profile in `PROFILES`
unconditionally. The `4k` `skipped_gate_failed` reason disappears (4K always runs). The
decode-path forensics gate is untouched and retains precedence: a 2K `violation` keeps
`cpu.gate_reason="decode_path_violation"` over `gate_failed`.

### D5: CPU keeps its shape; counts only when the gate passes

`runner.py` still samples CPU during the 2K round; `_build_cpu_block` is unchanged. CPU
`points` enter `objective_total` only on a gate pass; on a fail the existing
`gate_failed` override (already in `build_score`, now triggered by the new criterion)
applies unless a more specific reason wins.

## Risks / Trade-offs

- **Schema churn.** The `gate` block's criterion fields change and `skipped_gate_failed`
  disappears; downstream parsers of those break. Intended.
- **Wasted wall-clock.** 4K is always captured even when 2K decode is broken. Accepted:
  4K correctness is now required to evaluate the gate, and the user chose "run all cases."
- **`10/30` on a gate pass with bad performance may read as "lost points."** Mitigated by
  the report showing the gate as *passed* and FPS/CPU as the unfilled remainder.

## Migration Plan

Supersedes the `add-2k-level0-gate` behavior. No data migration; scores recompute per
run. Gate tests are rewritten for the new criterion/effect; decode-path forensics tests
stay green (forensics untouched).

## Resolved Questions

- **Report layout for a gate-passed-but-low-performance run** — distinguish "gate
  passed, performance points not earned" from "gate failed, performance not scored."
  The failed-gate banner explicitly says level-1 is not scored; a passing gate emits no
  such banner and reports the earned FPS/CPU values normally.
