## 1. Fixtures and failing tests (TDD red)

- [x] 1.1 Add `test_submissions/src/transcode_to_h264/` cheat fixture: pulls the fixed H.265 RTSP source, transcodes to H.264 server-side, serves it to a `<video>` in `[data-testid="player-video"]`. Wire it into `scripts/build_test_zips.sh`.
- [x] 1.2 ~~Add a legit WASM-HEVC reference fixture~~ **OBSOLETE — it already exists.** Implementation finding (2026-05-30): the bundled `submissions/self-test` native `video_server` contestant is NOT a transcoder — the instrumented evaluator confirmed it delivers **H.265 over WebSocket** to a canvas+WASM decoder (`verdict=ok`, `sinks=['hevc']`, no `<video>` decoder, scored 29.3/30). It IS the legit in-browser reference. The `captureChannel→encodeChannel` naming was a red herring. No new fixture needed; use this contestant as the positive case.
- [x] 1.3 Write failing unit test for the wire-codec classifier (`bytes -> {hevc|avc|raw|jpeg|unknown}`) with synthetic fMP4 init segments (`hvc1`/`avc1`), HEVC vs H.264 NAL headers, and a JPEG SOI sample.
- [x] 1.4 Write failing unit test for the scorer decode-path gate: a `violation` verdict zeros `correctness_points` and `fps_points` for the profile, and zeros CPU (`gate_reason="decode_path_violation"`) when the profile is `2k`; `ok`/`inconclusive`/absent leave scoring unchanged and `inconclusive` sets `review_required`.
- [x] 1.5 Write failing self-test expectation: `transcode_to_h264` MUST score `0` on both profiles; the legit WASM-HEVC fixture MUST score `> 0`.

## 2. Wire-codec classifier (pure)

- [x] 2.1 Implement the pure classifier module (`bytes -> codec-class`): fMP4 sample-entry box scan (`hvc1`/`hev1`/`avc1`), HEVC `nal_unit_type` vs H.264 NAL header detection, JPEG/raw detection. No browser deps.
- [x] 2.2 Make task 1.3's unit test pass; add edge cases (truncated/partial buffers → `unknown`, never a false `avc`/`hevc`).

## 3. Scorer decode-path gate

- [x] 3.1 In `scorer.py`, read `metrics["decode_forensics"]["verdict"]` per profile; on `violation` set `correctness_points=0` and `fps_points=0` and attach a `decode_path` sub-block (`verdict`, `checks`, `evidence`).
- [x] 3.2 On a `2k` violation, force the CPU block to `points=0, gated=true, gate_reason="decode_path_violation"`, taking precedence over the existing CPU gate order.
- [x] 3.3 Set top-level `score.json.review_required = true` when any profile verdict is `inconclusive`; default missing `decode_forensics` to `inconclusive` (fail-open, no score change).
- [x] 3.4 Make task 1.4's unit test pass; confirm existing scorer scenarios (CPU gating, 4K linear, 2K bands) still pass unchanged.

## 4. Runner forensics (Check 1 + Check 2)

- [x] 4.1 Add `HOST_DECODES_HEVC = False` constant + comment in `runner.py` documenting the Check-1 coupling.
- [x] 4.2 Check 1: open a CDP `Media` session before navigation, collect `Media.playerPropertiesChanged`; flag `violation` when a video decoder reports `kFramesDecoded > 0`; record the self-reported codec as evidence only. Wrap in `try/except` → degrade to "no signal".
- [x] 4.3 Check 2: inject `add_init_script` wrappers (`WebSocket`, `fetch`/`XHR`, `SourceBuffer.appendBuffer`, `VideoDecoder.configure`) that sample decode-path bytes, run them through the task-2 classifier, and report a summary via an exposed binding. Cap inspected bytes (init segments + first N KB) to protect capture throughput.
- [x] 4.4 Aggregate the per-profile verdict (`violation` if Check 1 fires or Check 2 saw non-H.265 into a sink; `ok` if Check 2 confirmed H.265; else `inconclusive`) and write `decode_forensics.json` alongside the screenshots. Ensure all forensic failures degrade to `inconclusive` and never fail the round.

## 5. Analyzer + report wiring

- [x] 5.1 In `analyzer.py`, pass `decode_forensics.json` (if present next to screenshots) through into `<profile>_metrics.json` under a `decode_forensics` key, mirroring the existing `capture_meta.json` → `cpu` pass-through.
- [x] 5.2 In `report.py`, render the per-profile decode-path verdict + evidence, and surface the top-level `review_required` flag prominently (internal report only).

## 6. Self-test harness rework

- [x] 6.1 Rework `scripts/test.sh` expectations: the existing native `video_server` reference's `total_ge:13` expectation STAYS valid (it is legit in-browser HEVC, verified 29.3/30). ADD a `transcode_to_h264` case asserting `total == 0` (or `total_le:0`) and `decode_path.verdict == "violation"` on both profiles.
- [x] 6.2 Update `test_submissions/REFERENCE_H265_NOTE.md` to describe the new in-browser reference and the intent-B enforcement.

## 7. End-to-end validation

- [x] 7.1 Run the `transcode_to_h264` fixture through `scripts/evaluator.sh`; confirm `score.json` shows `verdict="violation"` and `correctness_points=0`, `fps_points=0` on both profiles (and `cpu.gate_reason="decode_path_violation"` for 2k).
- [x] 7.2 Run the legit in-browser reference — **DONE 2026-05-30**: native `video_server` contestant scored `verdict="ok"`, `sinks=['hevc']`, 29.3/30, `review_required=false` on both profiles. No false positive.
- [x] 7.3 Forensics-absent / older run scores unchanged — **covered** by unit test `test_absent_forensics_is_backward_compatible` (and the failure-path run earlier preserved schema with `review_required` present).
- [x] 7.4 Hand off to the operator for the full self-test suite run (the full suite is ~20 min; iterate only on any failing case during development).
