## ADDED Requirements

### Requirement: Decode-Path Forensics

During each capture round, `runner.py` SHALL determine a per-profile `decode_path_verdict` ∈ {`ok`, `violation`, `inconclusive`} indicating whether the stream entering the browser's decode path is an H.265 (HEVC) elementary stream, and SHALL write it — with supporting evidence — to a `decode_forensics.json` artifact alongside the screenshots. `analyzer.py` SHALL pass this block through into `<profile>_metrics.json` under a `decode_forensics` key (the same pass-through pattern it uses for the runner's `cpu` block). The verdict SHALL be derived from two complementary checks:

- **Check 1 — active `<video>` decoder:** via the Chrome DevTools Protocol `Media` domain, the runner SHALL observe whether any media player has an active **video** decoder that has decoded one or more frames. Because the canonical host cannot decode HEVC through `<video>`/MSE/WebCodecs (`HOST_DECODES_HEVC = False`), a functioning `<video>` video decoder is itself proof that the input was re-encoded to a browser-decodable codec → `violation`. The self-reported codec string SHALL be recorded as evidence but SHALL NOT be the trigger. This rule is gated behind `HOST_DECODES_HEVC = False`; if the host policy ever enables hardware HEVC, Check 1 MUST instead inspect the codec string (flagging `avc1`, allowing `hvc1`/`hev1`).
- **Check 2 — wire-codec inspection:** instrumentation injected before contestant code (via `add_init_script`) SHALL sample bytes entering the decode path (`WebSocket`, `fetch`/`XHR` responses, `SourceBuffer.appendBuffer`, `VideoDecoder.configure`) and classify them. H.265-coded bytes (`hvc1`/`hev1` fMP4 sample entries, HEVC NAL unit types, or an HEVC `VideoDecoder` codec string) reaching a decode sink → confirms `ok`. Non-H.265 bytes reaching a decode/render sink (`avc1`, raw frames, JPEG) → `violation`. The byte classifier SHALL be a pure function so it is unit-testable without a browser.

Verdict aggregation per profile: `violation` if Check 1 fires OR Check 2 observed non-H.265 bytes into a sink; `ok` if Check 2 confirmed H.265 into a sink and nothing contradicts it; otherwise `inconclusive`. The forensic checks SHALL be best-effort: any error in the CDP session, the Media domain, or the injected binding SHALL degrade that check to "no signal" and SHALL NOT make the run fail or produce a `violation`. When `decode_forensics.json` is absent entirely (e.g. a pre-existing run), the scorer SHALL treat the verdict as `inconclusive`.

The policy is **fail-open**: only a positive `violation` deducts points (see the Scoring requirement). `ok` and `inconclusive` are scored on measured metrics; an `inconclusive` verdict on any profile SHALL set a top-level `review_required = true` in `score.json` and be flagged in `report.html` for manual review. The known evasion surface (dedicated Workers, WebTransport, obfuscation that `add_init_script` cannot reach) SHALL be documented; combined with fail-open, evasion yields false-negatives, never false-positives.

#### Scenario: Legal in-browser WASM decode is OK

- **WHEN** a contestant pulls the fixed H.265 RTSP source, relays/transmuxes the HEVC elementary stream into the page, and decodes it with an in-browser WASM (or WebCodecs) decoder rendering to `<canvas>` — creating no functioning `<video>` decoder
- **THEN** Check 1 produces no signal, Check 2 observes H.265-coded bytes into the decode sink, and `decode_forensics.json` records `verdict = "ok"` for that profile

#### Scenario: Server-side transcode to H.264 is a violation

- **WHEN** a contestant's backend transcodes the H.265 source to H.264 and the frontend plays it in a `<video>` element
- **THEN** the CDP `Media` domain reports a working video decoder with frames decoded, Check 1 fires, and `decode_forensics.json` records `verdict = "violation"` for that profile with the observed codec as evidence

#### Scenario: Server-side decode and push pixels is a violation

- **WHEN** a contestant's backend decodes the H.265 server-side and pushes raw frames or JPEG images to the page (no `<video>`, no H.265 on the wire)
- **THEN** Check 2 classifies the decode-path bytes as non-H.265 (raw/JPEG) and `decode_forensics.json` records `verdict = "violation"` for that profile

#### Scenario: Unobservable decode path is inconclusive and scored normally

- **WHEN** the decode path cannot be observed (e.g. bytes flow only through a dedicated Worker or a transport the injected instrumentation does not wrap) and no functioning `<video>` decoder is present
- **THEN** `decode_forensics.json` records `verdict = "inconclusive"`, the profile is scored on its measured metrics, and `score.json.review_required = true`

#### Scenario: Forensics never crashes the run

- **WHEN** the CDP `Media` session, the Media domain events, or the injected page binding raise an error
- **THEN** the affected check degrades to "no signal", the round still completes and produces screenshots, and the verdict is at worst `inconclusive` — never a `violation` caused by the error itself

## MODIFIED Requirements

### Requirement: Contestant Runtime Contract

Each contestant submission zip SHALL, after extraction into `dirname <submission_zip>`, provide an executable `start.sh` that launches whatever processes the submission needs; it MAY provide a `stop.sh` for cleanup. The evaluator SHALL export `RTSP_SERVER_HOST=127.0.0.1`, `RTSP_SERVER_PORT=554`, and `FRONTEND_PORT=8080` before invoking `start.sh`. Internal contestant processes (relay/transmux backends, demuxers, etc.) MAY bind any other local port; the evaluator neither prescribes nor cleans those — the contestant's process group is SIGKILLed as a whole at cleanup. The frontend SHALL expose route `/play` accepting `profile` (`2k` or `4k`) and `autoplay=1`, render the video into `[data-testid="player-video"]` at an actual rendered size of at least `1280x720` with proportional (uncropped) display, set `window.__PLAYER_READY__ = true` after the first frame is rendered, and assign a human-readable string to `window.__PLAYER_ERROR__` on playback failure. The RTSP source URLs that the contestant SHALL pull from are fixed: `rtsp://127.0.0.1:554/test/h265_2560_1440` for `profile=2k` and `rtsp://127.0.0.1:554/test/h265_3840_2160` for `profile=4k`.

The H.265 elementary stream SHALL be decoded **in the browser**. A contestant MAY relay, recontainerize, or transmux the HEVC stream server-side (e.g. RTSP→fMP4 keeping `hvc1`, with no re-encode) but SHALL NOT transcode it to another codec, nor decode it server-side and push decoded pixels (raw frames / images) to the page. The evaluator forensically inspects the codec of the stream entering the browser's decode path (see the Decode-Path Forensics requirement); a stream that is not H.265 at that boundary is a violation and zeros that profile's score.

The canonical evaluation host is Ubuntu 24.04 with Google Chrome (pinned version recorded in every `score.json` / `report.html` as `chromium_version`). The runtime contract is the same for both profiles — `[data-testid="player-video"]` with the readiness signals above. The evaluator does NOT prescribe a *rendering* strategy (`<canvas>`, `<video>`, OffscreenCanvas, WebGL, etc. are all the contestant's choice) provided the **decode** of H.265 happens in the browser. On the canonical host the browser cannot decode HEVC natively, so in practice the viable in-browser path is a contestant-supplied WASM (or, where supported, WebCodecs) HEVC decoder.

#### Scenario: Missing start.sh

- **WHEN** the extracted submission does not contain `start.sh`
- **THEN** the evaluator fails the submission with a clear `missing start.sh` error, records it in `evaluator.log`, and skips capture rounds

#### Scenario: Optional stop.sh

- **WHEN** the submission provides `stop.sh`
- **THEN** the evaluator invokes it during cleanup; **WHEN** it is absent **THEN** the evaluator proceeds with its own port/process cleanup without error

#### Scenario: Frontend never becomes ready

- **WHEN** `http://localhost:8080/play?profile=2k&autoplay=1` is not reachable within 60 seconds of `start.sh` returning
- **THEN** the evaluator assigns objective score 0 for the submission, records `startup timeout` in `evaluator.log`, and proceeds to cleanup

#### Scenario: Profile parameter routes to the correct stream

- **WHEN** the contestant frontend is opened at `http://localhost:8080/play?profile=4k&autoplay=1`
- **THEN** the contestant SHALL pull from `rtsp://127.0.0.1:554/test/h265_3840_2160` and render its decoded output into `[data-testid="player-video"]`; opening with `profile=2k` SHALL similarly route to `rtsp://127.0.0.1:554/test/h265_2560_1440`

#### Scenario: In-browser H.265 decode is required

- **WHEN** a submission delivers the H.265 elementary stream into the page and decodes it in the browser (WASM/WebCodecs), rendering into `[data-testid="player-video"]`
- **THEN** the submission satisfies the contract and is scored on its measured metrics; **WHEN** instead the submission delivers a non-H.265 stream (server-side transcode or server-side decode) to the browser **THEN** the affected profile is a Decode-Path Forensics violation and scores 0 on correctness and FPS

### Requirement: Playwright Capture Runner

`runner.py` SHALL accept `--profile` (one of the keys in `lib/profiles.py::PROFILES`, i.e. `2k` or `4k`), `--output`, `--duration`, and `--fps`; launch headless Chromium with `--disable-dev-shm-usage`, `--no-sandbox`, `--autoplay-policy=no-user-gesture-required`, and `--enable-features=PlatformHEVCDecoderSupport`; use a fresh browser context per profile at viewport `1280x720`; navigate to `http://localhost:8080/play?profile=<profile>&autoplay=1`; wait up to 15 seconds for `window.__PLAYER_READY__ === true`; capture element-only screenshots of `[data-testid="player-video"]` (not full-page) at the requested rate for the requested duration; write screenshots as `shot_NNNNN.jpg` and a `timestamps.json` containing per-shot capture timestamps, target capture parameters, capture strategy metadata, and any browser page errors. The runner MUST NOT use `networkidle` as a readiness condition.

Before navigation, the runner SHALL inject decode-path forensic instrumentation (`add_init_script`) and SHALL open a CDP `Media` session for the page, and at capture end SHALL write a `decode_forensics.json` artifact alongside the screenshots recording the per-profile `decode_path_verdict` and its evidence (see the Decode-Path Forensics requirement). Forensic collection SHALL be best-effort and SHALL NOT cause the round to fail.

The runner SHALL precompute the element clip once after readiness and MUST verify that the clip is at least `1280x720`. The default capture strategy SHALL remain Playwright page screenshots with `clip`, `type="jpeg"`, and a documented JPEG quality. The runner MAY expose a non-default Chrome DevTools Protocol screenshot strategy for benchmarking or operator tuning, provided the selected strategy is recorded in `timestamps.json` and preserves element-only clipping.

#### Scenario: Round succeeds

- **WHEN** the contestant frontend signals readiness within 15 seconds and the player element is present with a clip of at least `1280x720`
- **THEN** the runner produces approximately `--duration × --fps` screenshots in `--output/`, a `timestamps.json` with monotonically non-decreasing timestamps, the requested `target_fps`, the requested `target_duration_s`, and the selected capture strategy, and a `decode_forensics.json` recording the profile's `decode_path_verdict`

#### Scenario: Element below minimum capture size

- **WHEN** `[data-testid="player-video"]` is visible after readiness but its clip is smaller than `1280x720`
- **THEN** the runner fails the round with a clear `player-video below minimum size` error captured in `timestamps.json` and the evaluator log

#### Scenario: Readiness timeout

- **WHEN** `window.__PLAYER_READY__` is not `true` within 15 seconds
- **THEN** the runner reads `window.__PLAYER_ERROR__` if present, writes the reason and any captured browser errors into `timestamps.json`, exits non-zero, and the evaluator fails that profile round

#### Scenario: Missing player element

- **WHEN** `[data-testid="player-video"]` cannot be located after readiness
- **THEN** the runner fails the round with a clear `missing data-testid` error captured in `timestamps.json` and the evaluator log

#### Scenario: Profile selects the right URL

- **WHEN** `runner.py --profile 4k ...` is invoked
- **THEN** the navigated URL is exactly `http://localhost:8080/play?profile=4k&autoplay=1`; the runner SHALL NOT emit `?codec=` query parameters under any flag combination

#### Scenario: CDP strategy is auditable when enabled

- **WHEN** the non-default Chrome DevTools Protocol screenshot strategy is selected
- **THEN** the runner captures the same element clip, records that strategy and its relevant options in `timestamps.json`, and still writes screenshots using the same `shot_NNNNN.jpg` naming convention

#### Scenario: Forensic collection is non-fatal

- **WHEN** opening the CDP `Media` session or injecting the forensic instrumentation raises an error
- **THEN** the runner still completes the capture and writes screenshots, and `decode_forensics.json` records `verdict = "inconclusive"` rather than failing the round

### Requirement: Scoring

`scorer.py` SHALL accept one or more `--metrics PROFILE=PATH` arguments (one per profile, e.g. `--metrics 2k=results/.../2k_metrics.json --metrics 4k=results/.../4k_metrics.json`), `--output`, and `--report`; score 2K for up to 10 points (5 correctness + 5 FPS), score 4K for up to 15 points (5 correctness + 10 FPS), compute an additional 0-5 point CPU sub-score based on contestant CPU usage measured during the **2K profile** round (see the Contestant CPU Usage Measurement requirement); and produce a `score.json` containing one block per profile (keys `2k`, `4k`), a top-level `cpu` block, `objective_total`, and `max_score` of `30`.

**Decode-path gate** (first, per profile): when the profile's `metrics.decode_forensics.verdict == "violation"`, that profile's `correctness_points` AND `fps_points` SHALL both be `0` regardless of measured rates or FPS, and the profile block SHALL include a `decode_path` sub-block (`verdict`, `checks`, `evidence`). When the violating profile is `2k`, the CPU block SHALL additionally be set to `points=0, gated=true, gate_reason="decode_path_violation"`. A verdict of `ok`, `inconclusive`, or an absent `decode_forensics` block SHALL NOT affect scoring (fail-open); an `inconclusive` verdict on any profile SHALL set top-level `score.json.review_required = true`.

**Correctness** (rescaled): full 5 when `watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`; partial 2 when `watermark_recognition_rate >= 0.80` AND `mean_ssim >= 0.75`; otherwise 0. This applies to both 2K and 4K. 4K correctness SHALL remain scored even when 4K FPS is low — EXCEPT when that profile's decode-path verdict is `violation`, in which case both correctness and FPS are 0 per the decode-path gate.

**2K FPS** (threshold based): expected FPS SHALL come from `PROFILES["2k"].fps` and default to `20`. Full 5 when `measured_fps / expected_fps >= FPS_FULL_RATIO_BY_PROFILE["2k"]`; partial 3 when `measured_fps / expected_fps >= FPS_PARTIAL_RATIO_BY_PROFILE["2k"]`; otherwise 0. The default values SHALL remain `FPS_FULL_RATIO_BY_PROFILE["2k"] = 0.85` and `FPS_PARTIAL_RATIO_BY_PROFILE["2k"] = 0.50`. The 2K block in `score.json` SHALL include `fps_full_threshold_used`, `fps_partial_threshold_used`, and `expected_fps`.

**4K FPS** (linear absolute score): expected FPS SHALL come from `PROFILES["4k"].fps` and default to `20`. `score.json.4k.fps_points` SHALL equal `round(min(max(measured_fps, 0) / expected_fps, 1.0) * 10, 2)`. A 4K run measured at `4fps` against the default `20fps` expected value SHALL earn `2.0` FPS points. The 4K block in `score.json` SHALL include `fps_scoring_mode = "linear_absolute"`, `fps_linear_full_score = 10`, and `expected_fps`. 4K threshold-band fields MAY be omitted or set to `null`, but the report MUST make the linear formula clear.

**CPU** (gating on 2K): `scorer.score_cpu(mean_cpu_percent, measured_cpu_profile_fps, expected_cpu_profile_fps)` SHALL return an integer in `[0, 5]` together with a nullable `gate_reason` string, evaluated in this order:

1. Gate: if `measured_cpu_profile_fps / expected_cpu_profile_fps < CPU_GATE_FPS_RATIO` (default `0.65`), return `(0, "2k_fps_below_threshold")`. A submission that does not reach the minimum 2K playback throughput cannot earn any CPU points.
2. If `mean_cpu_percent` is unavailable (analyzer omitted the field, or `capture_meta.json.cpu` was null), return `(0, "sampler_no_data")`.
3. If `mean_cpu_percent <= CPU_FULL_THRESHOLD_PERCENT` (default `5.0`), return `(5, None)`.
4. If `mean_cpu_percent > CPU_ZERO_THRESHOLD_PERCENT` (default `20.0`), return `(0, None)`.
5. Otherwise return `(round((CPU_ZERO_THRESHOLD_PERCENT - mean_cpu_percent) / (CPU_ZERO_THRESHOLD_PERCENT - CPU_PARTIAL_START_PERCENT) * 5), None)`, clamped to `[0, 5]`. `CPU_PARTIAL_START_PERCENT` defaults to `6.0`.

A `2k` decode-path violation SHALL take precedence over the CPU gate order above and force `(0, "decode_path_violation")`.

The CPU block SHALL include `measured_on_profile = "2k"`, `gate_profile = "2k"`, `expected_fps`, `measured_fps`, `thresholds_used`, `mean_percent`, `sample_count`, and the existing audit fields. The six tunables (`CPU_GATE_FPS_RATIO`, `CPU_FULL_THRESHOLD_PERCENT`, `CPU_PARTIAL_START_PERCENT`, `CPU_ZERO_THRESHOLD_PERCENT`, `CPU_MIN_SAMPLES`, `_cpu_sampler.DEFAULT_SAMPLE_HZ`) SHALL be exposed as named module-level constants and the actual values applied to each run SHALL be recorded under `score.json.cpu.thresholds_used` so a contestant audit can verify which thresholds produced the score.

When the 2K round fails entirely (no `2k_metrics.json` produced, or it lacks the fields the gate inspects), `scorer.py` SHALL still emit a complete `score.json` with `cpu.points=0`, `cpu.gated=true`, `cpu.gate_reason="2k_round_failed"`, and `cpu.mean_percent=null`. When the host-side wrapper fails before the evaluator main body runs, the existing failure `score.json` path SHALL include `cpu.gated=true`, `cpu.gate_reason="host_failure"`, `cpu.points=0`.

`objective_total` SHALL equal `2k.total + 4k.total + cpu.points` and SHALL NOT exceed `max_score` (`30`). Because 4K FPS points can be fractional, `objective_total` MAY be fractional and SHALL be rounded consistently for JSON/report display.

#### Scenario: Per-profile totals

- **WHEN** scoring completes for a submission
- **THEN** `score.json` includes a `2k` block, a `4k` block, a top-level `cpu` block, an `objective_total` equal to `2k.total + 4k.total + cpu.points`, and `max_score = 30`, with the underlying metrics (rates, mean SSIM, measured FPS, CPU mean percent, sample count, thresholds/formulas applied) preserved for audit; the keys `h264` and `h265` SHALL NOT appear

#### Scenario: Decode-path violation zeros the profile

- **WHEN** a profile's `metrics.decode_forensics.verdict == "violation"`
- **THEN** that profile's `correctness_points = 0` and `fps_points = 0` regardless of measured rates/FPS, the profile block records a `decode_path` sub-block with the verdict and evidence, and — if the profile is `2k` — `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "decode_path_violation"`

#### Scenario: OK or inconclusive verdict does not change scoring

- **WHEN** a profile's decode-path verdict is `ok`, `inconclusive`, or absent
- **THEN** that profile is scored on its measured metrics exactly as it would be without forensics; an `inconclusive` verdict additionally sets `score.json.review_required = true`

#### Scenario: Fake-overlay submission

- **WHEN** a submission draws a fake canvas overlay that visually resembles the watermark but fails DataMatrix, color block, or SSIM checks
- **THEN** it cannot reach full correctness (5) per profile and may receive 2 or 0 depending on which checks it passes

#### Scenario: 2K FPS full credit uses 20fps expected value

- **WHEN** a submission's 2K round produces `measured_fps / expected_fps >= 0.85` (e.g. `17.2` fps against expected `20` fps = `86%`)
- **THEN** `score.json.2k.fps_points = 5`, `score.json.2k.expected_fps = 20`, `score.json.2k.fps_full_threshold_used = 0.85`, and `score.json.2k.fps_partial_threshold_used = 0.50`

#### Scenario: 2K FPS in the partial band receives 3 points

- **WHEN** a submission's 2K round produces `0.50 <= measured_fps / expected_fps < 0.85` (e.g. `12` fps / `20` fps = `60%`)
- **THEN** `score.json.2k.fps_points = 3`, `score.json.2k.expected_fps = 20`, `score.json.2k.fps_full_threshold_used = 0.85`, and `score.json.2k.fps_partial_threshold_used = 0.50`

#### Scenario: 2K FPS below the partial floor receives 0

- **WHEN** a submission's 2K round produces `measured_fps / expected_fps < 0.50` (e.g. `8` fps / `20` fps = `40%`)
- **THEN** `score.json.2k.fps_points = 0`, `score.json.2k.expected_fps = 20`, and `score.json.2k.fps_partial_threshold_used = 0.50`

#### Scenario: 4K correctness is still scored

- **WHEN** the 4K round produces low FPS but passes watermark, color block, and SSIM thresholds AND its decode-path verdict is not a `violation`
- **THEN** the 4K block can still receive 5 correctness points, and only the 4K FPS points are reduced by the linear formula

#### Scenario: 4K FPS is scored linearly by absolute FPS

- **WHEN** a submission's 4K round produces `measured_fps = 4.0` and `expected_fps = 20`
- **THEN** `score.json.4k.fps_points = 2.0`, `score.json.4k.fps_scoring_mode = "linear_absolute"`, `score.json.4k.expected_fps = 20`, and `score.json.4k.fps_linear_full_score = 10`

#### Scenario: 4K FPS linear score is capped

- **WHEN** a submission's 4K round produces `measured_fps >= 20`
- **THEN** `score.json.4k.fps_points = 10.0` and the value does not exceed 10 even if measured FPS is higher than the source FPS

#### Scenario: Missing profile entry fails loudly

- **WHEN** `scorer.score_fps` is invoked with a profile name absent from `FPS_FULL_RATIO_BY_PROFILE` or `FPS_PARTIAL_RATIO_BY_PROFILE`
- **THEN** `score_fps` raises a clear error (e.g. `KeyError`) rather than silently falling back to a default ratio, ensuring adding a new threshold-scored profile in `PROFILES` cannot bypass a scoring-policy decision

#### Scenario: Partial-below-full invariant is enforced at import time

- **WHEN** `scorer.py` is imported with a profile whose `FPS_PARTIAL_RATIO_BY_PROFILE[profile] >= FPS_FULL_RATIO_BY_PROFILE[profile]`
- **THEN** import fails with a clear assertion-style error naming the offending profile, so a configuration typo cannot collapse the partial band silently

#### Scenario: CPU gate trips when 2K does not meet the throughput gate

- **WHEN** the 2K round produces `measured_fps / expected_fps = 0.50` and `CPU_GATE_FPS_RATIO = 0.65`
- **THEN** `score.json.cpu.points = 0`, `score.json.cpu.gated = true`, `score.json.cpu.gate_reason = "2k_fps_below_threshold"`, `score.json.cpu.measured_on_profile = "2k"`, and the measured `mean_cpu_percent` is diagnostic only

#### Scenario: CPU full marks at low usage

- **WHEN** the 2K round produces `measured_fps / expected_fps >= CPU_GATE_FPS_RATIO` and `mean_cpu_percent <= 5.0`
- **THEN** `cpu.points = 5`, `cpu.gated = false`, `cpu.gate_reason = null`, `cpu.measured_on_profile = "2k"`, and `cpu.thresholds_used` records every threshold that produced this outcome

#### Scenario: CPU partial credit in the proportional band

- **WHEN** the 2K round produces `measured_fps / expected_fps >= CPU_GATE_FPS_RATIO` and `mean_cpu_percent` falls within `(5.0, 20.0]`
- **THEN** `cpu.points = round((20 - mean_cpu_percent) / 14 * 5)` clamped to `[0, 5]`, `cpu.gated = false`, and the formula matches the published score table at the integer percent breakpoints (`6->5`, `7->5`, `10->4`, `13->2`, `15->2`, `17->1`, `19->0`, `20->0`)

#### Scenario: CPU zero when usage exceeds the upper limit

- **WHEN** the 2K round produces `measured_fps / expected_fps >= CPU_GATE_FPS_RATIO` (passes the gate) AND `mean_cpu_percent > 20.0`
- **THEN** `cpu.points = 0`, `cpu.gated = false`, `cpu.gate_reason = null`, and `cpu.mean_percent` is recorded as-measured for audit (not clipped)

#### Scenario: CPU gated when sampler produced no data

- **WHEN** the 2K capture round ran but `capture_meta.json` is missing OR `capture_meta.json.cpu` is null OR fewer than `CPU_MIN_SAMPLES` samples were collected
- **THEN** `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "sampler_no_data"`, `cpu.mean_percent = null`, and `cpu.measured_on_profile = "2k"`

#### Scenario: CPU gated when sampled round itself failed

- **WHEN** no `2k_metrics.json` was produced
- **THEN** `score.json` is still emitted with `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "2k_round_failed"`, and `cpu.mean_percent = null`, alongside whatever `2k.reason` the runner recorded

#### Scenario: Thresholds-used audit field

- **WHEN** any `score.json` is produced
- **THEN** `score.json.cpu.thresholds_used` contains exactly the six keys `gate_fps_ratio`, `full_percent`, `partial_start_percent`, `zero_percent`, `min_samples`, `sample_hz`, each set to the value actually applied to that run, so an organizer can replay the score from the metrics alone

### Requirement: Anti-Cheating Behaviors

The evaluator SHALL detect or neutralize the cheating strategies listed below, and SHALL classify infrastructure problems separately from contestant failures.

#### Scenario: Static image playback

- **WHEN** a contestant renders one frozen image instead of decoding the stream
- **THEN** `unique_frame_count` collapses, `measured_fps` is near 0 for the affected profile, and FPS points are 0 for 2K while 4K receives only the linear FPS value implied by its near-zero measured FPS

#### Scenario: I-frame-only or repeated-frame playback

- **WHEN** a contestant only displays I-frames or repeats a small set of frames
- **THEN** `unique_frame_count` and `measured_fps` reflect the reduction, scoring partial or zero FPS points according to the 2K threshold policy and the 4K linear FPS policy

#### Scenario: Fake canvas overlay

- **WHEN** a contestant draws a watermark-like overlay but the actual decoded video is missing or wrong
- **THEN** DataMatrix recognition, the four color block checks, and SSIM cannot all pass together, so full correctness (5) per profile is unreachable

#### Scenario: Delayed or stale rendering

- **WHEN** the player shows old frames or stalls
- **THEN** `frame_numbers` in `timestamps.json` and the analyzer's frame-number-over-time series expose the gap, and the report flags the affected samples

#### Scenario: Missing `data-testid`

- **WHEN** the player element does not carry `data-testid="player-video"`
- **THEN** the round fails with a clear error and the evaluator does not silently capture the wrong element

#### Scenario: 2K-only or 4K-only decoder

- **WHEN** a contestant only supports one resolution and renders the other as static / black / overlay
- **THEN** the unsupported profile collapses `unique_frame_count` and SSIM independently of the supported profile, scoring low or zero on correctness/FPS for the failing profile while the passing profile is unaffected; if 2K is the failing profile, the CPU gate trips and `cpu.gate_reason="2k_fps_below_threshold"`

#### Scenario: Server-side transcode to a browser-native codec

- **WHEN** a contestant's backend transcodes the fixed H.265 source to H.264 (or any non-H.265 codec) and the frontend plays it — typically in a `<video>` element that decodes natively
- **THEN** Decode-Path Forensics records `verdict = "violation"` for the affected profile (Check 1 detects a functioning `<video>` decoder on a host that cannot decode HEVC), and the Scoring decode-path gate zeros that profile's correctness and FPS (and CPU for `2k`)

#### Scenario: Server-side decode pushing pixels

- **WHEN** a contestant's backend decodes the H.265 server-side and pushes raw frames or JPEG images to the page rather than delivering the H.265 elementary stream
- **THEN** Decode-Path Forensics Check 2 classifies the decode-path bytes as non-H.265 and records `verdict = "violation"`, and the Scoring decode-path gate zeros that profile's correctness and FPS
