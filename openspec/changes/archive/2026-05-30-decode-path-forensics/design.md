## Context

Motivation is in `proposal.md`. Current state: the evaluator is intentionally codec-blind at the contestant boundary — `runner.py` screenshots `[data-testid="player-video"]`, `analyzer.py` measures pixels, `scorer.py` bands them. Nothing observes what codec reaches the browser. The host's Chrome cannot decode HEVC in `<video>`/MSE/WebCodecs (confirmed: `MediaSource.isTypeSupported` false for all `hvc1`/`hev1`, true for `avc1`), so a server-side transcode-to-H.264 (or server-side decode + push-pixels) plays smoothly and inflates FPS while only partially denting correctness.

Constraints that shape the design:
- **Pipeline stages stay independently runnable** (CLAUDE.md). The forensic signal must flow through the same runner→artifact→analyzer-passthrough→scorer pipe already used for CPU (`runner.py` writes `capture_meta.json`; `analyzer.py:395` passes its `cpu` block through into `<profile>_metrics.json`; `scorer.py` consumes it). No new import-time coupling between stages.
- **Profile registry is the single truth source** — both `2k` and `4k` are H.265; there is no H.264 profile.
- **Host policy is fixed** (decision, this change): the eval Chrome is NOT given VA-API HEVC. This is load-bearing for Check 1 (below).
- **Fail-open** (decision): only positive violation evidence deducts.

## Goals / Non-Goals

**Goals:**
- Produce a per-profile `decode_path_verdict ∈ {ok, violation, inconclusive}` during capture, with evidence, and thread it to the scorer.
- Zero `correctness_points` and `fps_points` (and `cpu.points` for `2k`) when a profile's verdict is `violation`.
- Never penalize on absence of evidence; flag `inconclusive` for manual review.
- Be additive/backward-safe: a run with no forensic artifact scores exactly as today.

**Non-Goals:**
- A tamper-proof proof system. Check 2's injection surface is an arms race; we accept false-negatives, not false-positives.
- 4K SSIM calibration (separate change).
- Enabling hardware HEVC on the host (explicitly rejected; see proposal).
- Distinguishing *which* non-H.265 codec — any non-H.265 decode path is equally a violation.

## Decisions

### D1 — Produce the verdict in `runner.py`, thread it via the existing CPU-style pass-through

`runner.py` is the only stage with the live browser, so forensics is produced there. It writes a `decode_forensics.json` next to the screenshots (sibling of `capture_meta.json`). `analyzer.py` passes that block through into `<profile>_metrics.json` under a `decode_forensics` key, exactly as it already does for `cpu` (`analyzer.py:395-403`). `scorer.py` reads `metrics["decode_forensics"]["verdict"]`.

*Why over alternatives:* a new `scorer.py --forensics PROFILE=PATH` arg would also work, but reusing the runner→capture_meta→analyzer-passthrough→scorer path keeps the established side-signal convention and the "scorer is the consumer, stages don't import each other" invariant. Absent artifact → treated as `inconclusive` (fail-open, backward-safe).

### D2 — Check 1: any *working* `<video>` decoder is a violation (CDP Media domain), NOT codec-string matching

Open a CDP session on the page (the runner already uses CDP for the optional screenshot strategy, `runner.py:378`), enable the `Media` domain, and collect `Media.playerPropertiesChanged` events. If any media player reports an active **video** decoder that has decoded frames (`kFramesDecoded > 0` / a live `kVideoDecoderName`), Check 1 = `violation`. The reported codec string is recorded as *evidence* but is not the trigger.

*Why:* on this HEVC-incapable host a `<video>` element cannot legitimately be decoding our streams at all — so a *functioning* video player is itself proof the input was re-encoded to something playable (H.264). Keying on "decoder is working" rather than the self-reported codec string is both simpler and harder to spoof. The legal canvas+WASM path creates no media player → no Check-1 signal.

*Critical coupling:* this rule is valid **only because the host cannot decode HEVC**. It is gated behind the fixed host policy. If the host ever gains VA-API HEVC, Check 1 MUST switch to codec-string inspection (flag `avc1`, allow `hvc1`/`hev1`). Captured in Risks + Open Questions and asserted in code via a named constant `HOST_DECODES_HEVC = False`.

### D3 — Check 2: pre-load instrumentation classifies bytes entering the decode path

Inject via Playwright `context.add_init_script` (runs before any contestant script, in the main frame and sub-frames) wrappers around the decode-path entry points: `WebSocket` message/`send`, `fetch`/`XHR` response bodies, `MediaSource`→`SourceBuffer.appendBuffer`, and `VideoDecoder.configure`. A small pure classifier inspects sampled bytes:
- fMP4 init segment → presence of `hvc1`/`hev1` vs `avc1` sample-entry boxes.
- Raw NAL transport → HEVC `nal_unit_type` layout vs H.264 NAL header.
- Pre-decoded sinks → JPEG `FFD8` SOI, raw RGBA/`ImageData` sizes → server-side-decode signal.
- `VideoDecoder.configure({codec})` → codec string directly.

The page reports a compact classification summary back through a `page.expose_function` binding, read at capture end. The classifier is a **pure function** (`bytes → {hevc|avc|raw|jpeg|unknown}`) so it is unit-testable without a browser.

*Known gap (→ inconclusive, by design):* `add_init_script` does not run inside dedicated Workers, and WebTransport/obfuscation can evade. Unobserved decode path → no positive signal → `inconclusive` → fail-open.

### D4 — Verdict aggregation + scoring mapping

Per profile: `violation` if Check 1 fires OR Check 2 saw non-H.265 bytes feeding a decode/render sink; `ok` if Check 2 confirmed H.265 into the sink and nothing contradicts it; otherwise `inconclusive`. In `scorer.py`, `violation` forces `correctness_points = 0` and `fps_points = 0` for that profile, and for `2k` sets the CPU block to `points=0, gated=true, gate_reason="decode_path_violation"`. This is carved as an explicit exception into the existing rule *"4K correctness SHALL remain scored even when 4K FPS is low"* — that clause still holds for the low-FPS case, but a `violation` verdict overrides it. `score.json.<profile>` gains a `decode_path` block (`verdict`, `checks`, `evidence`); a top-level `review_required: true` is set when any profile is `inconclusive`.

### D5 — Errors degrade to `inconclusive`, never to `violation`

Any failure in the CDP session, the Media domain, or the injected binding is caught and degrades that check to "no signal" (mirroring the `_cpu_sampler` private-attribute `try/except` fallback at `runner.py:282`). Forensics must never make a run fail or falsely convict.

## Risks / Trade-offs

- **Evasion via Workers / WebTransport / obfuscation** → fail-open means evasion produces false-negatives, never false-positives. The hardened contract (spec now *requires* in-browser H.265 decode) gives organizers grounds to disqualify on manual review of `inconclusive`-flagged runs.
- **Check 1 is host-coupled** → "working `<video>` = violation" is only valid while `HOST_DECODES_HEVC = False`. Guarded by a named constant + comment; a host-policy change is a follow-up spec change, not a silent edit.
- **CDP Media domain reliability** → stable domain, but wrapped in `try/except` → degrade to `inconclusive`.
- **Capture-throughput overhead** → init-script wrappers + sampling could slow the 30 Hz loop; mitigate by inspecting only init segments + first N KB per buffer and reporting once at capture end, not per shot.
- **The bundled reference becomes a "cheat"** → the current `video_server` reference likely transcodes; its `total_ge:13` self-test expectation breaks. Mitigated by building a genuine WASM-HEVC reference *before* flipping the expectation (sequenced in tasks).

## Migration Plan

1. Land forensics additively: verdict defaults to `inconclusive` when the artifact is absent → existing legit runs score unchanged.
2. Build the in-browser WASM-HEVC reference contestant (prerequisite for validating the `ok` path end-to-end).
3. Add the `transcode_to_h264` cheat fixture (failing-first: it currently scores high, must drop to 0).
4. Flip `scripts/test.sh` expectations off the transcoding `video_server` reference; update `REFERENCE_H265_NOTE.md`.
Rollback: forensics is additive and fail-open; reverting the scorer's verdict consumption restores prior scores with no data migration.

## Open Questions

- Exact byte-sampling thresholds for "the decode sink was fed codec X" (how many sampled segments/bytes constitute a positive classification). Resolve empirically against the two fixtures during implementation.
- Whether to add backend-subtree-vs-browser-subtree CPU split as a *secondary* heuristic for server-side decode. Deferred — Check 1/2 are the spec'd primaries; note only.
- If host policy later enables VA-API HEVC, Check 1 flips to codec-string inspection — out of scope here, recorded for the future host-policy change.
