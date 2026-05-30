# Rescope the Level-0 Gate to Decode Correctness - Tasks

## 1. Tests first (failing)

- [x] 1.1 Rewrite `tests/test_gate.py` `gate_passed` cases: 2K corr=5 AND 4K corr=5 → True; either corr<5 → False; absent/empty 2K or 4K metrics → False; FPS value never affects the verdict
- [x] 1.2 `build_score` gate-pass case: `gate.passed == true`, per-profile `total == correctness + fps`, CPU scored, `objective_total == 2k.total + 4k.total + cpu.points`
- [x] 1.3 `build_score` gate-fail case: per-profile `total == correctness` only, `fps_points` still present, `cpu.points == 0` with `gated == true` and `gate_reason == "gate_failed"`, `objective_total == 2k.correctness + 4k.correctness`
- [x] 1.4 Perfect-run case: `2k` 5+5, `4k` 5+10, `cpu` 5 → `objective_total == 30`
- [x] 1.5 Gate-pass-but-poor-performance case: both corr=5, fps=0, cpu=0 → `objective_total == 10`
- [x] 1.6 Decode-path precedence case: 4K `violation` → 4K corr=0 → gate fails; a 2K `violation` keeps `cpu.gate_reason == "decode_path_violation"` (not `"gate_failed"`)
- [x] 1.7 Confirm tests fail for the right reason (`pytest tests/test_gate.py`)

## 2. scorer.py — gate decision + enforcement

- [x] 2.1 Redefine `gate_passed(profile_metrics)` to require `score_correctness(2k) == 5` AND `score_correctness(4k) == 5`; treat missing/incomplete metrics (or violation-zeroed correctness) as fail
- [x] 2.2 In `build_score`, derive the gate and emit the updated `gate` block (`passed`, `2k_correctness_points`, `4k_correctness_points`)
- [x] 2.3 On gate PASS: per-profile `total = correctness + fps`; CPU scored; `objective_total = 2k.total + 4k.total + cpu.points`
- [x] 2.4 On gate FAIL: per-profile `total = correctness` only (keep `fps_points` field); CPU → `points=0, gated, gate_reason="gate_failed"` unless a more specific reason wins (`decode_path_violation` etc.); `objective_total = 2k.correctness + 4k.correctness`
- [x] 2.5 Remove the `--gate-check` CLI mode and the old 2K-corr+2K-fps gate criterion / `skipped_gate_failed` 4K zeroing

## 3. scripts/evaluator.sh — remove the capture short-circuit

- [x] 3.1 Remove the gate-first ordering + `scorer.py --gate-check` query; capture and analyze every profile in `PROFILES` unconditionally
- [x] 3.2 Drop the `--profile-reason 4k=skipped_gate_failed` plumbing (no longer applicable)
- [x] 3.3 Confirm CPU sampling still runs in the 2K round and `cpu` is still emitted

## 4. report.py — surface the redefined gate

- [x] 4.1 Update the gate banner/summary to the correctness-based criterion (2K corr + 4K corr, passed/failed)
- [x] 4.2 On gate fail, show level-1 (FPS/CPU) as not scored; on gate pass with low performance, show points simply unearned (distinct states)
- [x] 4.3 Update/replace `tests/test_report_gate.py` for the new banner and correctness-only totals on a fail

## 5. Tests cleanup & full run

- [x] 5.1 Update `tests/test_decode_forensics_scoring.py` for the gate-conditioned total (violation → corr=0 → gate fail)
- [x] 5.2 Audit other tests for assertions assuming the old criterion or `skipped_gate_failed`; update expectations
- [x] 5.3 Run the focused scorer/report tests green
- [x] 5.4 `openspec validate rescope-scoring-decode-correctness --strict`
- [x] 5.5 Hand the full self-test suite (`scripts/test.sh`, ~20 min) to the operator for the end-to-end run
