# Self-test H.265 references and the decode-path enforcement

Internal note. Not shipped to contestants.

## Intent-B: H.265 must be decoded in the browser

The contest rewards plugin-free **in-browser** H.265 decode (WASM / WebCodecs).
The evaluator enforces this with Decode-Path Forensics (see
`openspec/specs/evaluator/spec.md` — Requirement: Decode-Path Forensics): it
inspects the codec of the stream entering the browser's decode path and, on a
positive violation, zeros that profile's correctness + FPS (and CPU for 2K).
The policy is **fail-open** — only positive violation evidence deducts.

On the canonical host Chrome cannot decode HEVC via `<video>`/MSE/WebCodecs
(only `avc1`), so the only viable legal path is a contestant-supplied WASM HEVC
decoder rendering to `<canvas>`.

## The legit reference: the bundled native contestant

The bundled `submissions/self-test` native `video_server` contestant is the
**legitimate in-browser reference**. Despite its `captureChannel → encodeChannel`
config naming, it does NOT transcode: it relays the **H.265 elementary stream
over WebSocket** to a canvas+WASM decoder. Verified via the instrumented
evaluator (2026-05-30): `decode_path.verdict = "ok"`, `sinks = ["hevc"]`, no
`<video>` decoder, scored **29.3 / 30** on both profiles.

## The cheat reference: `transcode_to_h264`

`test_submissions/src/transcode_to_h264/` is the negative case: it transcodes
the H.265 source to H.264 server-side and plays it in a native `<video>`.
Decode-Path Forensics Check 1 detects the active `<video>` decoder
(`kVideoDecoderName` on an HEVC-incapable host) → `verdict = "violation"`, and
the scorer zeros both profiles. Self-test expectation:
`verdict_2k:violation,verdict_4k:violation,total_le:0`.

(The previous version of this note described an `<video>`-based H.264 reference
scoring 15/30 under the old per-codec layout. That layout — and the H.264 round —
were removed by the codec→profile refactor; both profiles are H.265 now.)
