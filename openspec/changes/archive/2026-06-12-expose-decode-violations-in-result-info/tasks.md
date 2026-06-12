## 1. Tests

- [x] 1.1 Add a failing `result_info.py` unit test for a 2K decode-path violation that expects a `Decode Path Violations:` section in `info`.
- [x] 1.2 Add a failing unit test that multiple violating profiles each get one bounded, profile-labeled info line.
- [x] 1.3 Add a failing unit test that `ok`, `inconclusive`, and absent decode-path verdicts do not render the violation section.
- [x] 1.4 Add a failing sanitization/assertion test that raw forensic evidence arrays, Chromium version, measured image metrics, thresholds, and paths are not copied into `info`.

## 2. Implementation

- [x] 2.1 Add a small helper in `result_info.py` to collect profiles whose `decode_path.verdict` is `violation`.
- [x] 2.2 Add a reason-mapping helper that derives concise contestant-facing text from `decode_path.checks` and `decode_path.evidence`, with a generic fallback.
- [x] 2.3 Update `_build_info_lines` to render `Decode Path Violations:` only when at least one profile violates.
- [x] 2.4 Keep `_build_debug_lines` behavior unchanged except for existing diagnostics.

## 3. Verification

- [x] 3.1 Run targeted result-info tests.
- [x] 3.2 Run the full test suite or the smallest relevant suite if full tests are impractical.
- [x] 3.3 Run `openspec status --change expose-decode-violations-in-result-info` and confirm the change is apply-ready.
