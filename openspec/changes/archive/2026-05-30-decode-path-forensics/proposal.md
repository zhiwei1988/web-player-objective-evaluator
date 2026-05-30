## Why

The contest's intent is to reward **plugin-free in-browser H.265 decode** (WASM / WebCodecs). But the evaluator is deliberately codec-agnostic at the contestant boundary — it only screenshots `[data-testid="player-video"]` and never inspects what codec reaches the browser. On the canonical host Chrome cannot decode HEVC at all (empirically: `MediaSource.isTypeSupported` is `false` for every `hvc1`/`hev1` variant, `true` only for `avc1`), so a contestant can run a backend that **transcodes the H.265 RTSP source to H.264** (or decodes it server-side and pushes raw frames/JPEG) and let the browser play that. This games the FPS dimension — native H.264 decode is smooth, so FPS approaches full marks — while the only push-back is a partial correctness hit. The bundled `video_server` reference contestant appears to do exactly this (its channel config carries `captureChannel → encodeChannel`). A submission that does not deliver H.265 to the browser's decode path should not earn correctness or FPS points for that profile.

> **Implementation finding (2026-05-30, supersedes the assumption above):** the proposal initially suspected the bundled `video_server` reference of transcoding. The instrumented evaluator disproved this — that contestant delivers **H.265 over WebSocket** to a canvas+WASM decoder (`verdict=ok`, scored 29.3/30, no `<video>` decoder). It is the *legitimate* in-browser reference. The threat model and scoring gate are unchanged; only the fixture plan shrank — the legit reference already exists, so only a transcode *cheat* fixture remains to build.

## What Changes

- **BREAKING (contestant contract):** Narrow the frozen "codec-agnostic / any approach is the contestant's choice" stance. H.265 MUST be decoded **in the browser**; the evaluator forensically inspects the codec of the stream entering the browser's decode path. Recontainerizing/transmuxing the H.265 elementary stream (e.g. RTSP→fMP4 keeping `hvc1`, no re-encode) stays legal; transcoding to another codec or decoding server-side and pushing pixels is a violation.
- Add **decode-path forensics** to the capture stage, producing a per-profile `decode_path_verdict` ∈ {`ok`, `violation`, `inconclusive`} from two complementary checks:
  - **Check 1 — active `<video>` decoder (CDP Media domain):** on this HEVC-incapable host, any `<video>` element actually decoding and producing frames is itself proof of non-H.265 input → `violation`. No codec-string parsing needed.
  - **Check 2 — in-page byte sniffing:** instrumentation injected before contestant code inspects bytes entering the decode path (WebSocket, `fetch`/`Response`, `SourceBuffer.appendBuffer`, Worker `postMessage`, `VideoDecoder` config). HEVC-coded bytes → confirms `ok`; non-H.265 bytes (H.264 / raw / JPEG) → `violation`; unobservable → `inconclusive`.
- **Fail-open policy:** only **positive violation** evidence deducts. A `violation` verdict for a profile zeros that profile's `correctness_points` AND `fps_points`; for the `2k` profile it additionally zeros the CPU sub-score (the sampled round is tainted). `inconclusive`/`ok` are scored normally; `inconclusive` is flagged in `report.html` for manual review. We never require positive H.265 evidence to award points.
- Surface the verdict and its evidence in `score.json` (per-profile) and `report.html`.
- Add self-test fixtures and tests (failing-first): a `transcode_to_h264` cheat fixture that MUST score 0 on both profiles, and a genuine **in-browser WASM-HEVC reference** contestant that MUST still score > 0 (it does not yet exist; it is a prerequisite for validating the legal path end-to-end). The existing `video_server`-based reference expectation (`total_ge:13`) must be reworked since that submission is likely a transcoder under the new rule.

Out of scope (separate future change): 4K SSIM calibration — a *legitimate* WASM-HEVC contestant still gets only partial 4K correctness because its canvas is downscaled to the ~1280px capture element vs the 3840px reference. That is a matched-resolution / threshold recalibration concern, sequenced after this change.

## Capabilities

### New Capabilities
<!-- none — the forensics behavior is added as a new Requirement within the existing evaluator capability -->

### Modified Capabilities
- `evaluator`: **ADD** a `Decode-Path Forensics` requirement (two checks, per-profile verdict, fail-open). **MODIFY** `Contestant Runtime Contract` (require in-browser H.265 decode; evaluator inspects the wire codec). **MODIFY** `Scoring` (a `violation` verdict forces `correctness_points=0` and `fps_points=0` for that profile, and `cpu.points=0` when `2k` is in violation; carve this exception into the existing "4K correctness SHALL remain scored even when FPS is low" clause). **MODIFY** `Playwright Capture Runner` (inject the pre-load forensic instrumentation and open a CDP Media session, recording the verdict + evidence). **MODIFY** `Anti-Cheating Behaviors` (add server-side transcode / server-side decode as detected strategies).

## Impact

- **Spec:** `openspec/specs/evaluator/spec.md` — one new requirement, four modified requirements (incl. the frozen Contestant Runtime Contract, which is why this is an OpenSpec change).
- **Code:** `runner.py` (CDP Media-domain session + pre-load page/Worker instrumentation; emit per-profile `decode_path_verdict` + evidence into `timestamps.json`/a forensics artifact); `scorer.py` (consume the verdict; zero correctness/FPS per profile and CPU for `2k` on violation; new `score.json` fields); `report.py` (render verdict, evidence, and the `inconclusive` review flag); `analyzer.py` (pass-through of the forensic artifact if it co-locates with screenshots, mirroring the existing `capture_meta.json` CPU pass-through).
- **Fixtures/tests:** new `test_submissions/src/transcode_to_h264/` (expected 0) and a genuine in-browser WASM-HEVC reference under `test_submissions/src/` (expected > 0); `scripts/test.sh` / `scripts/build_test_zips.sh` expectations reworked away from the current transcoding `video_server` reference; update `REFERENCE_H265_NOTE.md`.
- **Contestant-facing:** the runtime contract tightens — submissions relying on a server-side transcode/decode shim will now score 0 per affected profile. No change to URL params, env vars, fixed RTSP URLs, or DOM signals.
- **Robustness caveat (documented, not a defect):** Check 2's injection surface is an arms race (Workers / WebTransport / obfuscation can evade). Combined with fail-open, evasion yields false-negatives, never false-positives — acceptable for an appeals-defensible organizer scorer.
