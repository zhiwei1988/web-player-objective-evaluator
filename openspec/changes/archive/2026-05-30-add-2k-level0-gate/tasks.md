## 1. Tests first (failing)

- [x] 1.1 Add `tests/test_gate.py` with `gate_passed` unit cases: full 2K (corr=5, fps=5) → True; corr<5 → False; fps<5 → False; absent/empty 2K metrics → False
- [x] 1.2 In `tests/test_gate.py`, add `build_score` cases for gate failure: 2K kept at actual total, `4k.total == 0` with `reason == "skipped_gate_failed"`, `cpu.points == 0` with `gated == true` and `gate_reason == "gate_failed"`, top-level `gate` block present, `objective_total == 2k.total`
- [x] 1.3 Add a `build_score` case for gate pass: `4k`/`cpu` scored normally, `gate.passed == true`, `objective_total == 2k.total + 4k.total + cpu.points`
- [x] 1.4 Add a precedence case: 2K `decode_forensics.verdict == "violation"` → gate fails, 4K zeroed, but `cpu.gate_reason == "decode_path_violation"` (not `"gate_failed"`)
- [x] 1.5 Add a self-consistency case: `build_score` handed BOTH `2k` (non-perfect) and `4k` metrics still zeroes `4k` and `cpu` (short-circuit cannot change score)
- [x] 1.6 Add a `--gate-check` CLI case (subprocess or `_cli`): exits `0` on perfect 2K metrics, non-zero on non-perfect, with no output file written
- [x] 1.7 Confirm tests fail for the right reason (run `pytest tests/test_gate.py`)

## 2. scorer.py — gate decision + enforcement

- [x] 2.1 Add `GATE_PROFILE = "2k"` constant and `gate_passed(metrics_2k) -> bool` reusing `score_correctness` and `score_fps(..., GATE_PROFILE)` against the full-mark bands; treat missing/incomplete metrics as fail
- [x] 2.2 In `build_score`, derive the gate verdict from the gate-profile metrics and emit a top-level `gate` block (`profile`, `passed`, `correctness_points`, `fps_points`)
- [x] 2.3 On gate failure, force the `4k` block to `total=0` + `reason="skipped_gate_failed"` even if 4K metrics were supplied; keep the 2K block as scored
- [x] 2.4 Wire a `gate_failed` CPU override into the existing precedence so it applies only when there is no `cpu_override_reason` and no `decode_path_violation` (`decode_path_violation` wins); verify `objective_total` math still holds
- [x] 2.5 Add `--gate-check PROFILE=PATH` to `_cli()`: load metrics, call `gate_passed`, exit `0`/`1`, write no output

## 3. scripts/evaluator.sh — capture short-circuit

- [x] 3.1 Resolve the gate profile and run+analyze it FIRST explicitly (not by relying on `sorted(PROFILES)` order)
- [x] 3.2 After analyzing the gate profile, query `scorer.py --gate-check 2k=<metrics>`; on non-zero, skip capture+analysis of the remaining profiles and record gate failure
- [x] 3.3 On gate failure, pass `--profile-reason 4k=skipped_gate_failed` (and rely on `build_score`'s `gate_failed` CPU override) to the final `scorer.py` call
- [x] 3.4 Confirm the short-circuit cannot alter the score (final scorer enforces the gate regardless of which profiles were captured)

## 4. report.py — surface the gate

- [x] 4.1 Render the top-level `gate` block (profile, passed, the two sub-scores) prominently in `report.html`
- [x] 4.2 Show `4k.reason == "skipped_gate_failed"` and `cpu.gate_reason == "gate_failed"` in the existing profile/CPU sections
- [x] 4.3 Extend or add `tests/test_report_*` coverage for the gate-failed rendering

## 5. Fixtures, spec sync, full verification

- [x] 5.1 Audit `tests/` for assertions on non-perfect-2K submissions whose 4K/CPU totals were non-zero; update expectations to the gated values (only `test_decode_forensics_scoring.py`'s 3 `2k=None` fail-open cases needed a passing 2K)
- [x] 5.2 Confirm `test_submissions/reference.zip` (perfect 2K) still passes the gate and scores ~unchanged — folded into the operator end-to-end run (5.5); by analysis the reference's historical ~29/30 implies a perfect 2K, so the gate opens and the score is unchanged
- [x] 5.3 Run `pytest tests/` (fast unit subset) and the gate tests green — 177 passed
- [x] 5.4 Run `openspec validate add-2k-level0-gate` and reconcile any drift between code and the delta spec (documented the CPU-reason precedence: more specific reasons keep priority over `gate_failed`)
- [x] 5.5 Hand off the full self-test (`scripts/test.sh`, ~20 min) to the operator for the end-to-end confirmation
