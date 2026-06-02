## Context

`result.info` is the contestant-facing projection of `score.json`. It currently reports the itemized point values and sanitized execution feedback, while decode-path details remain in `score.json`, `report.html`, and per-profile `decode_forensics.json`.

When decode-path forensics returns `violation`, `scorer.py` correctly zeros the affected profile's correctness and FPS. The current `result.info` output does not explain that zero in the `info` block, so contestants see a zero correctness line even when the debug metrics show watermark, color, and SSIM values that would otherwise score.

## Goals / Non-Goals

**Goals:**

- Surface profile-level decode-path violations in `result.info` `info` so contestants can understand why correctness/FPS were zeroed.
- Keep the text concise, bounded, and sanitized.
- Preserve organizer-only details in `debug`, `score.json`, `report.html`, and `decode_forensics.json`.

**Non-Goals:**

- Do not change scoring, decode-path forensics, or artifact schemas.
- Do not surface `ok` or `inconclusive` forensic verdicts in contestant-visible info.
- Do not expose raw evidence dumps, full codec traces, internal paths, Chromium versions, thresholds, or measured image metrics in `info`.

## Decisions

1. Add a dedicated `Decode Path Violations:` section to the `info` block only when at least one profile has `decode_path.verdict = "violation"`.

   This keeps normal result.info output unchanged and makes zeroed profiles explainable. Alternatives considered: appending text to each correctness line, or exposing decode-path evidence in `debug` only. Appending to score lines makes parsing and readability worse; debug-only output does not solve the contestant-facing confusion.

2. Render one bounded line per affected profile.

   The line should include the profile label and a short reason, such as `2K: browser decode path received H.264/AVC instead of H.265`. This is enough for contestants to act without exposing raw forensic internals.

3. Derive the reason from sanitized `decode_path.checks` and `decode_path.evidence`.

   If `sink_codecs` includes `avc`, say the browser decode path received H.264/AVC. If evidence/checks indicate an active browser video decoder on a host that cannot decode HEVC, say the browser video decoder was active for a non-HEVC-compatible path. If neither maps cleanly, fall back to `decode-path violation detected`.

4. Keep raw forensic evidence out of `info`.

   Raw evidence remains available in `score.json`, `report.html`, and profile artifacts. This preserves the existing separation between contestant-facing feedback and organizer-facing diagnostics.

## Risks / Trade-offs

- A concise reason can omit nuance when multiple forensic signals are present. Mitigation: include the strongest actionable signal and keep full evidence in internal artifacts.
- Contestants may want exact evidence strings. Mitigation: `debug` and artifacts remain available to organizers for appeals without leaking internal diagnostics by default.
- Future forensic signal names may not match the renderer's mapping. Mitigation: provide a generic fallback and add tests for known signal combinations.
