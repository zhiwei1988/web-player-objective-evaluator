## Context

The evaluator captures and scores every profile in `lib/profiles.py::PROFILES`
unconditionally (`scripts/evaluator.sh` loops `sorted(PROFILES)` = `[2k, 4k]`,
then `scorer.py` scores all metrics at once). The 2K round is the baseline
competency check, but a broken submission still pays the wall-clock cost of the
heavier 4K capture and can bank 4K/CPU points. We want 2K correctness + 2K FPS to
be a level-0 gate.

Two constraints shape the design:
- **Single truth source for scoring.** Per `CLAUDE.md`, scoring thresholds and
  policy live in `scorer.py` (+ spec). The gate criterion reuses the *existing*
  full-mark thresholds, so it must not introduce a parallel set of constants.
- **Stages independently runnable.** `scripts/evaluator.sh` invokes pipeline
  modules as subprocesses, never imports them. The gate query from the shell must
  therefore be a subprocess call, not embedded Python.

The user has fixed two semantics: (1) on gate failure, only the *downstream*
items (4K, CPU) are zeroed — the 2K block keeps its actual partial score; (2) the
gate criterion is exactly the existing full-mark bands (`correctness == 5` AND
`fps == 5`).

## Goals / Non-Goals

**Goals:**
- 2K correctness AND 2K FPS at full marks gate the 4K round and CPU sub-score.
- On gate failure: skip 4K *capture* (save wall-clock), keep 2K's real score,
  force 4K and CPU to 0, emit a clear contestant-visible reason.
- Gate decision owned by `scorer.py` (one criterion function), so the score is
  identical whether or not 4K was actually captured.
- `scripts/evaluator.sh` short-circuit is a pure optimization that cannot alter
  the score.

**Non-Goals:**
- No new scoring tunables or thresholds; no change to how 2K, 4K (when run), or
  CPU bands compute.
- No generalized N-level gate framework or `level`/`gate` field on `ProfileSpec`
  — there are two profiles; a single named `GATE_PROFILE = "2k"` constant suffices.
- No change to the contestant runtime contract (URLs, env vars, DOM signals).

## Decisions

### D1: Gate criterion lives in `scorer.py` as `gate_passed()`, reusing full-mark bands

`gate_passed(metrics_2k)` returns `score_correctness(...) == 5 and
score_fps(..., "2k") == 5`. It calls the same functions that produce the published
sub-scores, so the gate can never drift from the full-mark definition.
`GATE_PROFILE = "2k"` is a module constant (mirrors the existing
`CPU_PROFILE = next(... cpu_sampled ...)` pattern). Absent/incomplete 2K metrics →
gate fails (treated as not-full).

*Alternative considered:* a dedicated `GATE_*` threshold set decoupled from the
full-mark bands — rejected because the user chose "沿用现有满分阈值" and a second
set would be a divergence hazard (the exact failure mode `CLAUDE.md` warns about).

### D2: `build_score` enforces the gate independently of captured profiles

`build_score` computes the gate verdict from the 2K metrics it is handed, then:
keeps the 2K block as scored; if the gate fails, forces the `4k` block to
`{total: 0, reason: "skipped_gate_failed", ...}` (even if 4K metrics were passed
in) and routes CPU through a `gate_failed` override. A top-level `gate` block is
added. The existing `objective_total = 2k.total + 4k.total + cpu.points` formula is
*unchanged* — it simply sums zeroed downstream values on failure.

This makes the shell short-circuit safe: whether 4K capture ran or not, the score
is the same. It also makes a manual `scorer.py --metrics 2k=… --metrics 4k=…`
self-consistent.

*Alternative considered:* let the shell decide the score (zero 4K/CPU only when it
skips capture) — rejected; it splits scoring policy across bash and Python and
diverges on manual scorer runs.

### D3: CPU override precedence — `decode_path_violation` > `gate_failed`

CPU gating already has an ordered precedence in `_build_cpu_block`
(`cpu_override_reason` first). The decode-path violation override
(`decode_path_violation`) must continue to win when 2K has a forensics violation
(which also fails the gate). So `gate_failed` is applied only when there is no
decode-path override. Concretely: `cpu_effective_override = cpu_override_reason or
cpu_decode_override or gate_failed_override or host_failure`. Both the
decode-violation and gate-failure cases yield `points = 0`; only the
`gate_reason` string differs, which matters for the report and audit.

### D4: Shell short-circuit via `scorer.py --gate-check`

`scorer.py` gains a `--gate-check PROFILE=PATH` mode that loads the metrics, calls
`gate_passed`, and exits `0` (pass) / `1` (fail) with no file output. The capture
loop in `scripts/evaluator.sh` is restructured:

```
GATE_PROFILE=2k   (queried from PROFILES via the existing python one-liner pattern)
run+analyze the gate profile first
if scorer.py --gate-check 2k=<metrics>:  → continue to remaining profiles
else:                                     → skip remaining capture; mark gate-failed
final scorer.py call: add --profile-reason 4k=skipped_gate_failed when gate failed
```

The loop must run the gate profile first explicitly. Today `sorted()` happens to
put `2k` before `4k`, but a future `1080p` profile would sort first; ordering by
"gate profile, then the rest" removes that latent bug.

### D5: `score.json` / `report.html` surface

`score.json` (contestant-visible) additions: top-level `gate` block; `4k.reason =
"skipped_gate_failed"`; `cpu.gate_reason = "gate_failed"`. `report.html` renders the
gate verdict prominently and shows the skipped/gated reasons — the report already
has a per-profile `reason` row and a CPU gate callout to extend.

## Risks / Trade-offs

- **[Bimodal score distribution / unreachable band]** Gate-pass always means
  `2k.total = 10`, so totals land in `0–8` (gate failed) or `10–30` (gate passed);
  `9` is unreachable. → Accepted; it is the intended "perfect-2K-or-no-downstream"
  semantics, and `report.html` makes the reason explicit.
- **[Existing test fixtures break]** Fixtures with an intentionally non-perfect 2K
  previously asserted non-zero 4K/CPU totals; those now drop to 0. → Audit and
  update fixture expectations during implementation (TDD). The reference submission
  (perfect 2K, ~29/30) passes the gate and is unaffected.
- **[CPU sampled then discarded on failure]** The 2K capture always runs the CPU
  sampler, so a gate failure wastes that sampling work. → Negligible; CPU sampling
  rides along the 2K capture that must happen anyway, and the saved cost (4K
  capture) is the expensive part.
- **[Decode-path vs gate reason confusion]** Two different `gate_reason` strings can
  both mean "CPU = 0". → D3 fixes precedence deterministically; scenarios pin both.

## Migration Plan

Pure additive scoring-policy change; no data migration. Deploy by updating
`scorer.py`, `scripts/evaluator.sh`, `report.py`, and the spec. Re-run the
self-test suite to confirm the reference submission still passes the gate and that
fixtures with non-perfect 2K now report `skipped_gate_failed`. Rollback = revert the
commit; `score.json` consumers that ignore unknown keys are unaffected by the new
`gate` block.

## Open Questions

None — the two governing semantics (downstream-only zeroing; reuse of existing
full-mark thresholds) were resolved before proposal.
