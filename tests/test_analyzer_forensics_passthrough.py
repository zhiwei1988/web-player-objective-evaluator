"""The analyzer passes the runner's decode_forensics.json through into the
profile metrics JSON, mirroring how it forwards capture_meta.json's cpu block.
"""

from __future__ import annotations

import json

import analyzer


def test_load_decode_forensics_reads_sidecar(tmp_path):
    (tmp_path / "decode_forensics.json").write_text(
        json.dumps({"verdict": "violation", "checks": {"video_decoder_active": True}, "evidence": ["x"]})
    )
    out = analyzer._load_decode_forensics(tmp_path)
    assert out["verdict"] == "violation"
    assert out["checks"]["video_decoder_active"] is True


def test_load_decode_forensics_absent_returns_none(tmp_path):
    assert analyzer._load_decode_forensics(tmp_path) is None


def test_load_decode_forensics_corrupt_returns_none(tmp_path):
    (tmp_path / "decode_forensics.json").write_text("{not valid json")
    assert analyzer._load_decode_forensics(tmp_path) is None
