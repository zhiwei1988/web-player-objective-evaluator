# Why the bundled reference scores 15/30, not 30/30

Internal note. Not shipped to contestants. The spec does NOT prescribe any
rendering strategy.

The bundled `reference.zip` exercises the evaluator pipeline against a
known-good frontend that uses `<video>`. On the canonical evaluation host
(Ubuntu 24.04 + Google Chrome, no hardware HEVC decoder), `<video>` reports
`supported: false` for HEVC, so the reference's H.265 round fails with
`DEMUXER_ERROR_NO_SUPPORTED_STREAMS`. H.264 plays natively and scores 15/15.

`scripts/test.sh` accepts this with `total_ge:13` so the self-test passes.

If we ever need a fully-passing H.265 reference (e.g. to validate the H.265
analyzer / scorer code paths end-to-end against a known-good frame stream
on this host), the implementation would live under
`test_submissions/src/reference/web/` and would not change the spec — the
runtime contract is `[data-testid="player-video"]` with the readiness signals
regardless of rendering strategy.
