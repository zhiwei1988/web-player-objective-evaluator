## 1. Failing tests (TDD red)

- [x] 1.1 In `tests/test_result_info.py`, add a fixture/helper that writes `<run_dir>/2k_screenshots/capture_status.json` and `<run_dir>/4k_screenshots/capture_status.json` under a `tmp_path` run dir.
- [x] 1.2 Add a test asserting `render(..., run_dir=<tmp>)` always emits a `Capture Status:` section with one `2K` line and one `4K` line, in that order, on a successful run (`phase=completed` for both).
- [x] 1.3 Add a test asserting a profile whose status has a `detail` dict renders the phase plus the verbatim `detail` JSON on the line (e.g. `- 4K: navigation_timeout {"url": "http://..."}`), using deterministic key ordering.
- [x] 1.4 Add a test asserting internal-only phase labels (`forensics_installed`) and sensitive detail values (e.g. `chromium_version`, selector, clip coords) pass through unsanitized.
- [x] 1.5 Add a test asserting a missing `<profile>_screenshots/capture_status.json` (and `run_dir=None`) renders `- <LABEL>: not run` for that profile without raising, and still renders the other profile.
- [x] 1.6 Add a test asserting the `Capture Status:` block is contained within `|info|` (appears before the `|debug|` marker) per the line-oriented field protocol.
- [x] 1.7 Run `tests/test_result_info.py` and confirm the new tests fail for the right reason (block absent), while pre-existing tests still pass.

## 2. Implementation (TDD green)

- [x] 2.1 In `result_info.py`, add a helper that, given `run_dir`, reads each profile's `<profile>_screenshots/capture_status.json` and returns `Capture Status:` lines (verbatim `detail` via `json.dumps(..., ensure_ascii=False, sort_keys=True)`; `not run` when absent/unparseable/`run_dir=None`). Reuse `_PROFILE_LABELS`.
- [x] 2.2 Thread `run_dir` into `_build_info_lines` (or call the helper from `render`) and append the `Capture Status:` block after the existing info blocks, following the blank-line-then-heading pattern.
- [x] 2.3 Update the `result_info.py` module docstring from "pure projection of `score.json`" to "projection of `score.json` + `capture_status.json` read from `run_dir`".
- [x] 2.4 Run `tests/test_result_info.py`; confirm all tests (new + pre-existing) pass.

## 3. Spec sync & verification

- [x] 3.1 Confirm rendered output matches the spec delta's `Capture Status:` example and the modified scenarios in `specs/evaluator/spec.md`.
- [x] 3.2 Run `openspec validate expose-capture-status-in-result-info --strict` and resolve any issues.
- [x] 3.3 Run the full Python test suite to confirm no regression in `report.py` / lifecycle consumers (no behavior change expected there).
