"""Decode-path forensics: pure wire-codec classification + verdict aggregation.

Backs the evaluator's Decode-Path Forensics requirement. The browser-side
instrumentation (injected by runner.py) only *samples* bytes entering the
decode path and reports the active-decoder state; all classification and the
per-profile verdict decision live here so they are unit-testable without a
browser.

The classifier is deliberately conservative: it returns a positive codec class
only on an unambiguous signal and "unknown" otherwise. Combined with the
fail-open policy, ambiguity never convicts.
"""

from __future__ import annotations


# Whether the canonical evaluation host's Chrome can decode HEVC natively
# (via <video>/MSE/WebCodecs). It cannot — confirmed empirically
# (MediaSource.isTypeSupported is false for every hvc1/hev1 variant). This is
# load-bearing for Check 1 in decide_verdict(): a *functioning* <video> video
# decoder is itself proof the input was re-encoded to a browser-decodable codec.
# If the host policy ever enables VA-API HEVC, flip this to True and Check 1
# MUST switch to codec-string inspection instead (see decide_verdict).
HOST_DECODES_HEVC = False


VERDICT_OK = "ok"
VERDICT_VIOLATION = "violation"
VERDICT_INCONCLUSIVE = "inconclusive"

# Coded classes that, reaching a decode/render sink, prove the stream is NOT
# H.265 at the browser boundary.
_NON_HEVC_SINK_CLASSES = frozenset({"avc", "jpeg"})


def classify_codec(data: bytes) -> str:
    """Classify a sampled buffer as one of: 'hevc', 'avc', 'jpeg', 'unknown'.

    Recognizes:
      - JPEG by its SOI marker,
      - fMP4 sample-entry boxes ('hvc1'/'hev1' vs 'avc1'/'avc3'),
      - Annex-B and length-prefixed NAL units, disambiguated via parameter-set
        NAL types (HEVC VPS/SPS/PPS = 32/33/34; H.264 SPS/PPS = 7/8).

    Returns 'unknown' on anything ambiguous or too short — never a guess.
    """
    if not data:
        return "unknown"

    # JPEG (server-side-decoded frame pushed as an image).
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"

    # fMP4 sample-entry boxes. HEVC checked first; the tags are unambiguous.
    if b"hvc1" in data or b"hev1" in data:
        return "hevc"
    if b"avc1" in data or b"avc3" in data:
        return "avc"

    cls = _classify_nal(data)
    if cls:
        return cls

    return "unknown"


def _classify_nal(data: bytes) -> str | None:
    """Inspect NAL headers for an Annex-B or length-prefixed elementary stream."""
    header = _first_annexb_nal_header(data)
    if header is None:
        header = _first_length_prefixed_nal_header(data)
    if header is None:
        return None

    forbidden_zero_bit = header & 0x80
    if forbidden_zero_bit:
        return None  # not a valid NAL header in either codec

    hevc_type = (header >> 1) & 0x3F
    h264_type = header & 0x1F

    # Parameter sets are the cleanest discriminator.
    if hevc_type in (32, 33, 34):  # VPS / SPS / PPS (HEVC has VPS; H.264 does not)
        return "hevc"
    if h264_type in (7, 8):  # SPS / PPS (H.264)
        return "avc"
    # Fall back to slice/IDR NAL types.
    if hevc_type in (19, 20, 21):  # IDR_W_RADL / IDR_N_LP / CRA (HEVC)
        return "hevc"
    if h264_type in (1, 5):  # non-IDR / IDR slice (H.264)
        return "avc"
    return None


def _first_annexb_nal_header(data: bytes) -> int | None:
    """Return the NAL header byte following the first Annex-B start code, if the
    payload byte is actually present (a bare trailing start code is not a NAL)."""
    for start in (b"\x00\x00\x00\x01", b"\x00\x00\x01"):
        idx = data.find(start)
        if idx != -1:
            hdr = idx + len(start)
            if hdr < len(data):
                return data[hdr]
    return None


def _first_length_prefixed_nal_header(data: bytes) -> int | None:
    """Treat the first 4 bytes as a big-endian NAL length (AVCC/HVCC framing)
    and return the NAL header byte if the length is plausible."""
    if len(data) < 5:
        return None
    length = int.from_bytes(data[:4], "big")
    if 1 <= length <= len(data) - 4:
        return data[4]
    return None


def decide_verdict(checks: dict, host_decodes_hevc: bool = HOST_DECODES_HEVC) -> str:
    """Aggregate the two checks into a per-profile verdict.

    checks:
      - video_decoder_active: bool   (Check 1 — a <video> decoder produced frames)
      - sink_codecs: list[str]       (Check 2 — codec classes seen feeding a sink)

    Policy (fail-open): only positive violation evidence convicts.
      - Check 1 fires (host cannot decode HEVC, yet a <video> decoder works) -> violation
      - any non-HEVC coded bytes (avc/jpeg) reached a sink                   -> violation
      - HEVC bytes confirmed into a sink and nothing contradicts             -> ok
      - otherwise                                                            -> inconclusive
    """
    if not host_decodes_hevc and checks.get("video_decoder_active"):
        return VERDICT_VIOLATION

    sink_codecs = set(checks.get("sink_codecs") or [])
    if sink_codecs & _NON_HEVC_SINK_CLASSES:
        return VERDICT_VIOLATION
    if "hevc" in sink_codecs:
        return VERDICT_OK
    return VERDICT_INCONCLUSIVE


class ForensicsCollector:
    """Accumulate browser-side forensic samples into a per-profile verdict.

    The page instrumentation injected by runner.py feeds sampled decode-path
    bytes, `VideoDecoder.configure` codec strings, and CDP Media decoder state
    here; `result()` classifies and aggregates into the dict the runner writes
    to decode_forensics.json. Kept pure so the runner's forensic glue is
    unit-testable without a browser. Every `note_*` method is total — it never
    raises on bad input, so a malformed sample can't break a capture round.
    """

    _MAX_EVIDENCE = 50
    _MAX_ERRORS = 20

    def __init__(self, host_decodes_hevc: bool = HOST_DECODES_HEVC) -> None:
        self._host_decodes_hevc = host_decodes_hevc
        self._video_decoder_active = False
        self._video_decoder_codec: str | None = None
        self._sink_codecs: set[str] = set()
        self._evidence: list[str] = []
        self._errors: list[str] = []

    def note_video_decoder(
        self, codec: str | None = None, frames_decoded: int = 0, present: bool = False
    ) -> None:
        """Check 1: a CDP Media player reported a working <video> video decoder.

        `present` means Chrome selected/initialized a video decoder (reported as
        `kVideoDecoderName`) — which only happens for a codec it can actually
        decode. On a host that cannot decode HEVC, that alone is the violation
        signal. `frames_decoded > 0` is an equivalent positive signal on Chrome
        builds that surface a frame counter.
        """
        try:
            active = bool(present) or (frames_decoded and int(frames_decoded) > 0)
        except (TypeError, ValueError) as exc:
            self.note_error(f"note_video_decoder: {exc}")
            active = bool(present)
        if active:
            self._video_decoder_active = True
            if codec:
                self._video_decoder_codec = codec
            self._record_evidence(
                f"video_decoder:{codec or '?'}:frames={frames_decoded}:present={present}"
            )

    def note_sink_bytes(self, data: bytes, sink: str = "?") -> None:
        """Check 2: sampled bytes entering a decode/render sink."""
        try:
            cls = classify_codec(bytes(data))
            if cls != "unknown":
                self._sink_codecs.add(cls)
                self._record_evidence(f"sink:{sink}:{cls}")
        except (TypeError, ValueError) as exc:
            self.note_error(f"note_sink_bytes: {exc}")

    def note_sink_codec_string(self, codec_string: str, sink: str = "videodecoder") -> None:
        """Check 2: a `VideoDecoder.configure({codec})` codec string."""
        c = (codec_string or "").lower()
        if c.startswith(("hvc1", "hev1")):
            self._sink_codecs.add("hevc")
        elif c.startswith(("avc1", "avc3")):
            self._sink_codecs.add("avc")
        self._record_evidence(f"sink:{sink}:codec={codec_string}")

    def note_error(self, msg: str) -> None:
        if len(self._errors) < self._MAX_ERRORS:
            self._errors.append(str(msg))

    def _record_evidence(self, msg: str) -> None:
        if len(self._evidence) < self._MAX_EVIDENCE:
            self._evidence.append(msg)

    def result(self) -> dict:
        checks = {
            "video_decoder_active": self._video_decoder_active,
            "video_decoder_codec": self._video_decoder_codec,
            "sink_codecs": sorted(self._sink_codecs),
        }
        return {
            "verdict": decide_verdict(checks, host_decodes_hevc=self._host_decodes_hevc),
            "checks": checks,
            "evidence": list(self._evidence),
            "errors": list(self._errors),
            "host_decodes_hevc": self._host_decodes_hevc,
        }
