## Why

The fixed evaluation host cannot sustain the current 25fps screenshot and 4K playback workload reliably enough for 4K FPS and CPU efficiency to remain defensible as absolute performance scores. We need to keep the contest objective and auditable while adapting the scoring method to the host's observed capture and playback constraints.

## What Changes

- Change generated test video frame rate from 25fps to 20fps for both 2K and 4K profiles.
- Move contestant CPU usage scoring from the 4K round to the 2K round.
- Keep 4K as a decode-correctness test so submissions must still render the 4K stream correctly.
- Change 4K FPS scoring to a linear absolute-value score out of 5 points, where a 4fps result earns `4 / 20 * 5 = 1.0` point.
- Keep 2K as the primary FPS and CPU-efficiency workload under the new 20fps target.
- Update reports and score artifacts so the applied FPS target, CPU profile, and 4K linear FPS formula are explicit.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `evaluator`: Modify profile generation, capture/scoring, CPU measurement, and reporting requirements for a constrained fixed evaluation host.

## Impact

- Affected files likely include `lib/profiles.py`, `lib/watermark.py`, `scripts/prepare_streams.sh`, `scripts/evaluator.sh`, `runner.py`, `analyzer.py`, `scorer.py`, `report.py`, README documentation, and tests under `tests/`.
- Existing generated streams and reference frames must be regenerated after implementation because profile FPS changes from 25 to 20.
- Existing historical results remain interpretable under their recorded thresholds but should not be mixed directly with results produced after this change.
