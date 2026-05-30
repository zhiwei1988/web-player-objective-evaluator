"""Tests for lib.decode_forensics: the pure wire-codec classifier and the
per-profile verdict aggregator that back the Decode-Path Forensics requirement.

No browser dependency — the in-page instrumentation only samples bytes; the
classification and verdict logic live here so they are unit-testable.
"""

from __future__ import annotations

import pytest

from lib import decode_forensics as df


# ---- classify_codec ----------------------------------------------------------

def test_classify_hevc_fmp4_sample_entry():
    # fMP4 init segment carries an 'hvc1' (or 'hev1') sample-entry box.
    blob = b"\x00\x00\x00\x20stsd\x00\x00\x00\x01\x00\x00\x00\x10hvc1\x00\x00"
    assert df.classify_codec(blob) == "hevc"


def test_classify_avc_fmp4_sample_entry():
    blob = b"\x00\x00\x00\x20stsd\x00\x00\x00\x01\x00\x00\x00\x10avc1\x00\x00"
    assert df.classify_codec(blob) == "avc"


def test_classify_hevc_annexb_vps_nal():
    # Annex-B start code + HEVC VPS NAL (nal_unit_type 32). 0x40 -> (0x40>>1)&0x3f = 32.
    blob = b"\x00\x00\x00\x01\x40\x01\x0c\x01\xff\xff"
    assert df.classify_codec(blob) == "hevc"


def test_classify_avc_annexb_sps_nal():
    # Annex-B start code + H.264 SPS NAL (nal_unit_type 7). 0x67 & 0x1f = 7.
    blob = b"\x00\x00\x00\x01\x67\x42\xc0\x1e\xda"
    assert df.classify_codec(blob) == "avc"


def test_classify_jpeg():
    assert df.classify_codec(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "jpeg"


def test_classify_unknown_for_noise():
    assert df.classify_codec(b"\x00" * 40) == "unknown"
    assert df.classify_codec(b"") == "unknown"
    assert df.classify_codec(b"hello world not a codec") == "unknown"


def test_classify_never_false_positive_on_truncated():
    # A truncated start code must not be misread as a valid NAL.
    assert df.classify_codec(b"\x00\x00\x01") == "unknown"


# ---- decide_verdict ----------------------------------------------------------

def test_verdict_constants_are_distinct():
    assert {df.VERDICT_OK, df.VERDICT_VIOLATION, df.VERDICT_INCONCLUSIVE} == {
        "ok", "violation", "inconclusive",
    }


def test_verdict_violation_when_video_decoder_active_on_hevc_incapable_host():
    # Check 1: a functioning <video> decoder on a host that cannot decode HEVC.
    v = df.decide_verdict(
        {"video_decoder_active": True, "video_decoder_codec": "avc1.640028", "sink_codecs": []},
        host_decodes_hevc=False,
    )
    assert v == df.VERDICT_VIOLATION


def test_verdict_violation_when_non_hevc_bytes_reach_sink():
    v = df.decide_verdict(
        {"video_decoder_active": False, "sink_codecs": ["avc"]},
        host_decodes_hevc=False,
    )
    assert v == df.VERDICT_VIOLATION

    v2 = df.decide_verdict(
        {"video_decoder_active": False, "sink_codecs": ["jpeg"]},
        host_decodes_hevc=False,
    )
    assert v2 == df.VERDICT_VIOLATION


def test_verdict_ok_when_hevc_confirmed_into_sink_and_no_video_decoder():
    v = df.decide_verdict(
        {"video_decoder_active": False, "sink_codecs": ["hevc"]},
        host_decodes_hevc=False,
    )
    assert v == df.VERDICT_OK


def test_verdict_inconclusive_when_nothing_observed():
    v = df.decide_verdict(
        {"video_decoder_active": False, "sink_codecs": []},
        host_decodes_hevc=False,
    )
    assert v == df.VERDICT_INCONCLUSIVE

    # unknown sink bytes alone are not a positive signal either way
    v2 = df.decide_verdict(
        {"video_decoder_active": False, "sink_codecs": ["unknown"]},
        host_decodes_hevc=False,
    )
    assert v2 == df.VERDICT_INCONCLUSIVE


def test_verdict_video_decoder_allowed_when_host_decodes_hevc():
    # Guard for a future host-policy flip: an active <video> decoder is only a
    # violation because the host cannot decode HEVC. With HEVC-capable hosts,
    # Check 1 must not fire on the mere existence of a working decoder.
    v = df.decide_verdict(
        {"video_decoder_active": True, "video_decoder_codec": "hvc1.1.6.L153.B0", "sink_codecs": []},
        host_decodes_hevc=True,
    )
    assert v != df.VERDICT_VIOLATION


def test_host_decodes_hevc_default_is_false():
    assert df.HOST_DECODES_HEVC is False


# ---- ForensicsCollector ------------------------------------------------------

def test_collector_fresh_is_inconclusive():
    c = df.ForensicsCollector()
    assert c.result()["verdict"] == df.VERDICT_INCONCLUSIVE


def test_collector_active_video_decoder_is_violation():
    c = df.ForensicsCollector()
    c.note_video_decoder(codec="avc1.640028", frames_decoded=12)
    out = c.result()
    assert out["verdict"] == df.VERDICT_VIOLATION
    assert out["checks"]["video_decoder_active"] is True


def test_collector_idle_video_decoder_not_active():
    c = df.ForensicsCollector()
    c.note_video_decoder(codec="avc1.640028", frames_decoded=0)
    assert c.result()["verdict"] == df.VERDICT_INCONCLUSIVE


def test_collector_hevc_bytes_into_sink_is_ok():
    c = df.ForensicsCollector()
    c.note_sink_bytes(b"\x00\x00\x00\x01\x40\x01\x0c\x01", sink="websocket")
    assert c.result()["verdict"] == df.VERDICT_OK


def test_collector_avc_bytes_into_sink_is_violation():
    c = df.ForensicsCollector()
    c.note_sink_bytes(b"...stsd....avc1....", sink="appendBuffer")
    assert c.result()["verdict"] == df.VERDICT_VIOLATION


def test_collector_videodecoder_codec_string():
    c = df.ForensicsCollector()
    c.note_sink_codec_string("hvc1.1.6.L153.B0")
    assert c.result()["verdict"] == df.VERDICT_OK

    c2 = df.ForensicsCollector()
    c2.note_sink_codec_string("avc1.640028")
    assert c2.result()["verdict"] == df.VERDICT_VIOLATION


def test_collector_errors_never_raise_and_are_recorded():
    c = df.ForensicsCollector()
    c.note_error("media domain failed")
    out = c.result()
    assert out["verdict"] == df.VERDICT_INCONCLUSIVE  # error alone never convicts
    assert "media domain failed" in out["errors"]


def test_collector_result_shape():
    c = df.ForensicsCollector()
    out = c.result()
    assert set(out) >= {"verdict", "checks", "evidence", "errors"}
    assert set(out["checks"]) >= {"video_decoder_active", "sink_codecs"}
