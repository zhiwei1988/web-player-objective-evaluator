## Context

`result.info` is the contest-platform projection published beside the submission zip. Its `info` block is the contestant-visible surface, but the current evaluator contract restricts that block to the objective total and five item scores. Failure reasons and runtime diagnostics are available in `score.json`, profile `timestamps.json`, `contestant.log`, `report.html`, and the `debug` block, but those surfaces are not reliably visible to contestants.

The main gap is contestant-side execution failure feedback: missing `start.sh`, frontend readiness timeout, missing `data-testid=player-video`, startup timeout with `window.__PLAYER_ERROR__`, and selected frontend console/network failures. These are useful to contestants, while raw internal diagnostics such as run directories, host paths, Chromium version, full Playwright exceptions, thresholds, and organizer metrics should remain out of `info`.

## Goals / Non-Goals

**Goals:**

- Add a concise, contestant-visible execution feedback section to `result.info` when a contestant-side startup or capture failure occurs.
- Preserve the existing score breakdown and field protocol.
- Keep feedback deterministic, bounded, and sanitized before it reaches `result.info`.
- Keep full audit diagnostics in `debug`, `score.json`, `report.html`, logs, and profile artifacts.
- Make feedback generation testable without depending on a full browser run.

**Non-Goals:**

- Do not expose organizer-only metrics, thresholds, host paths, run directories, Chromium version, or full internal stack traces in `info`.
- Do not reclassify infrastructure failures as contestant failures.
- Do not make `result_info.py` read logs, timestamps, screenshots, or other side files directly.
- Do not change the `result`, `score`, `runtime`, `info`, `debug` field order.

## Decisions

### Decision 1: Store sanitized feedback in `score.json`

Add an optional top-level `contestant_feedback` field to `score.json`. The field is a list of short strings that have already been selected, sanitized, and bounded by the evaluator/scorer pipeline. `result_info.py` will only render this field.

Rationale: `result.info` is already a projection of `score.json` plus run metadata. Keeping the renderer pure avoids shell-side parsing in the renderer and makes unit tests simple. It also makes the visible feedback auditable from the authoritative score artifact.

Alternative considered: Have `result_info.py` read `contestant.log` or profile `timestamps.json` directly. This couples rendering to run-directory layout and makes publication behavior harder to test.

### Decision 2: Feedback is contestant-side only

Feedback in `info` is generated for failures attributable to the submitted work. Infrastructure, host, evaluator, RTSP, and publication failures keep their detailed reasons in `debug` only and use `result=1` when the score is untrusted.

Rationale: Contestants need actionable information about their own submission. Internal failures can be misleading and may expose host details.

Alternative considered: Always mirror any failure reason into `info`. This is simpler but violates the existing separation between contestant-visible information and organizer diagnostics.

### Decision 3: Use bounded sources with fixed limits

Candidate sources:

- Top-level contestant failure reason.
- Per-profile capture reason from `timestamps.json`.
- A concise value from `window.__PLAYER_ERROR__` when present.
- Selected browser diagnostics already captured in profile artifacts.
- Tail lines from `contestant.log` on startup/frontend readiness failures.

Each source should be normalized into short lines. The implementation should enforce both a per-line limit and total section limit before writing `contestant_feedback`.

Rationale: Logs can contain unbounded output, stack traces, ANSI escapes, secrets, host paths, and noisy dependency output. Fixed limits keep `result.info` useful and stable.

Alternative considered: Include the full log tail verbatim. This helps debugging in some cases but is too noisy and can leak unrelated host or environment details.

### Decision 4: Render as an optional `Execution Feedback:` section

When `score.json.contestant_feedback` contains entries, append the following section after the five-item breakdown and before `|debug|`:

```text

Execution Feedback:
- <feedback line>
- <feedback line>
```

When no feedback is available, keep the current `info` block unchanged.

Rationale: This preserves backward-compatible parsing of existing score lines while making failures visible in the same field contestants already see.

## Risks / Trade-offs

- Sanitization can hide useful details -> Keep full diagnostics in `debug`, `score.json`, run logs, and profile artifacts for organizer triage.
- Feedback can still include user-provided text -> Strip control characters, cap line/section length, and avoid adding host-derived fields.
- Some failures have no meaningful feedback -> Render the existing score breakdown without an empty feedback section.
- Tests may overfit exact wording -> Assert stable section labels, limits, and source inclusion while keeping detailed wording centralized in helper functions.
