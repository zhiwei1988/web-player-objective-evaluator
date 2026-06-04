## Why

When a contestant submission fails (or stalls) during capture, the contestant-visible `|info|` field of `result.info` today carries only a coarse `Execution Feedback:` line derived from a small pattern-matched subset of failure reasons. The richer per-phase signal that `runner.py` already records in `<profile>_screenshots/capture_status.json` (`phase` + `detail`) never reaches the contestant, so contestants cannot tell *where* their player got stuck (navigation, readiness, player-element lookup, clip, screenshot loop). We want that capture phase and its detail exposed directly in `|info|` for both profiles, on every run.

## What Changes

- `result_info.py` reads `<run_dir>/2k_screenshots/capture_status.json` and `<run_dir>/4k_screenshots/capture_status.json` and renders a new `Capture Status:` block in the `|info|` field — one line per profile, always (including `phase=completed` on success).
- The rendered detail is **raw and unfiltered**: the full `detail` dict is emitted verbatim, and all phase labels — including internal-only ones such as `forensics_installed` — pass through unchanged. No whitelist, no sanitization.
- **BREAKING (contract)**: the `result.info` `|info|` field contract is relaxed. The frozen spec currently forbids raw internal diagnostics in `|info|` (Chromium version, host paths, organizer-only failure details, measured FPS, CPU mean percent, etc.). This change explicitly authorizes `|info|` to carry the full per-profile capture `phase` + `detail`, exempting that block from the sanitization prohibition. Consequence, recorded deliberately: `|info|` shifts from "contestant-friendly sanitized summary" to "may carry raw internal diagnostics."
- Reconcile existing drift: commit `7f9b230` already added a `Runtime Metrics:` block (`measured_fps`, `cpu mean_percent`) to `|info|` (`result_info.py:137-149`), which violates the current spec MUST-NOT list. The spec is amended to register that block as authorized, eliminating the code↔spec divergence.
- `result_info.py`'s module docstring is updated from "pure projection of `score.json`" to "projection of `score.json` + `capture_status.json` read from `run_dir`".

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `evaluator`: the **Contest Platform Result Info** requirement changes — `|info|` is authorized to carry an always-present per-profile capture `phase` + raw `detail` block plus the already-shipped runtime-metrics block, and the corresponding "SHALL NOT place raw internal diagnostics in `info`" prohibition is narrowed to exclude these authorized blocks.

## Impact

- **Code**: `result_info.py` (new read of `capture_status.json` from `run_dir`; new `Capture Status:` render; docstring). `tests/test_result_info.py` (new coverage).
- **Spec**: `openspec/specs/evaluator/spec.md` — "Contest Platform Result Info" requirement (currently ~lines 547–625, esp. the MUST-NOT clause at 570 and the conciseness clause at 902).
- **Data source (unchanged)**: `runner.py:904 _write_capture_status` continues to write `{profile, phase, updated_at_epoch, detail}`; this change only consumes it. Existing consumers `report.py:255` and `scripts/diagnose_run.py:160` are unaffected.
- **Contract**: `result.info` is a frozen contestant contract; this is an intentional, spec-governed relaxation, not a silent edit.
- **No new plumbing**: `result_info.py` already receives `--run-dir` via `scripts/_result_info_lifecycle.sh`.
