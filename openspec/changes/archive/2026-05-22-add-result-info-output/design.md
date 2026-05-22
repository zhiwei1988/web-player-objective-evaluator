## Context

The evaluator currently produces `score.json` as the machine-readable score and `report.html` as the organizer-only audit report. Contest infrastructure also needs a `result.info` file in the directory that contains the submitted zip. `reference/result-sample.info` defines the expected line-oriented format with `result`, `score`, `runtime`, `info`, and `debug` fields.

`submissions/<team_id>/` is internal staging only. In the real contest, uploaded submissions live elsewhere, so the publication target must be derived from the original `<submission_zip>` path, not the staging directory.

## Goals / Non-Goals

**Goals:**

- Produce a deterministic `result.info` for every evaluator invocation that reaches a run directory and score generation path.
- Keep a copy in the run directory and publish a copy to `dirname <submission_zip>/result.info`.
- Make `|info|` directly useful to contestants by showing total score and each scoring item.
- Keep detailed metrics and failure diagnostics out of contestant-facing `info`, while preserving them in `debug`, `score.json`, and `report.html`.
- Preserve the existing `scripts/evaluator.sh <team_id> <submission_zip>` CLI.

**Non-Goals:**

- Replacing `score.json` as the authoritative structured score.
- Publishing `report.html` to contestants.
- Copying `result.info` into `submissions/<team_id>/`.
- Adding new scoring categories or changing scoring policy.

## Decisions

### Decision 1: Render `result.info` from `score.json`

`result.info` will be generated as a projection of the completed `score.json`, plus runtime and run-directory context.

Rationale: `score.json` is already the source of truth for profile, CPU, total, and failure-state scoring. Rendering from it avoids duplicating scoring logic in shell and keeps normal and failure-mode output consistent.

Alternative considered: write `result.info` directly in `scripts/evaluator.sh`. This is brittle because shell-side JSON parsing and formatting would duplicate scorer behavior.

### Decision 2: Add a focused renderer module

Add a small Python renderer, for example `result_info.py`, with a pure function that accepts a score dict and runtime/run metadata and returns the file content. `scripts/evaluator.sh` invokes it after `score.json` exists.

Rationale: a pure renderer is easy to unit test first, keeps formatting logic out of the shell script, and can be reused for both normal scoring and failure scoring.

Alternative considered: extend `scorer.py` to also write `result.info`. This would mix scoring/report generation with evaluator-level concerns such as wall-clock runtime and copying to the submitted zip directory.

### Decision 3: `info` contains only contestant-visible scoring items

The `|info|` field is multi-line and starts on the line after a standalone `|info|` marker:

```text
|info|
Objective Score: 23.5 / 30
Breakdown:
- 2K Correctness: 5 / 5
- 2K FPS: 5 / 5
- 4K Correctness: 5 / 5
- 4K FPS: 1.5 / 5
- CPU: 7 / 10
```

Rationale: contestants need a direct breakdown of where points were awarded, but not internal metrics, thresholds, host details, or debug reasons.

### Decision 4: `debug` carries organizer diagnostics

The `|debug|` field can be multi-line. It should include enough context to correlate `result.info` with internal artifacts: run directory, top-level reason, per-profile measured FPS and metric rates when present, CPU gate reason, CPU mean/sample count when present, and Chromium version.

Rationale: the sample format separates user-facing `info` from author-facing `debug`; keeping this split prevents `info` from becoming noisy while preserving fast triage data.

### Decision 5: Result code follows judge-result semantics

`|result|0` means the evaluator produced a valid contestant result, including valid zero-score outcomes caused by the contestant submission. `|result|1` means the evaluator/infrastructure failed and the score should not be trusted.

Rationale: `reference/result-sample.info` uses `result=0` with `score=0` for wrong-answer style outcomes. Contestant frontend unavailable and missing runtime contract are analogous contestant failures, not infrastructure failures.

## Risks / Trade-offs

- Copying to `dirname <submission_zip>` can overwrite a prior `result.info` in that upload directory. This is intentional platform behavior; the run directory preserves the audit copy.
- A failed copy can leave `results/<run>/result.info` present while the platform directory lacks it. The evaluator should log the copy failure and exit non-zero so operators can rerun or fix permissions.
- Formatting from `score.json` means malformed or missing score fields degrade the `result.info` breakdown. The renderer should default missing item scores to zero and put raw anomalies in `debug`.
