from __future__ import annotations

import json
from pathlib import Path

from render_snapshot_publication import publish_rendered_snapshots


def _write_score(path: Path) -> None:
    path.write_text(json.dumps({"max_score": 30, "objective_total": 19.5}))


def _write_shots(run_dir: Path, profile: str, names: list[str]) -> dict[str, bytes]:
    shots_dir = run_dir / f"{profile}_screenshots"
    shots_dir.mkdir(parents=True)
    payloads: dict[str, bytes] = {}
    for name in names:
        data = f"{profile}:{name}".encode("ascii")
        payloads[name] = data
        (shots_dir / name).write_bytes(data)
    return payloads


def test_publish_rendered_snapshots_copies_fixed_middle_shot(tmp_path):
    run_dir = tmp_path / "results" / "team_20260602"
    run_dir.mkdir(parents=True)
    score_path = run_dir / "score.json"
    _write_score(score_path)
    payloads_2k = _write_shots(
        run_dir,
        "2k",
        ["shot_00001.jpg", "shot_00002.jpg", "shot_00003.jpg"],
    )
    payloads_4k = _write_shots(
        run_dir,
        "4k",
        ["shot_00001.png", "shot_00002.png", "shot_00003.png"],
    )
    submission_dir = tmp_path / "RunCode" / "2079591"
    submission_dir.mkdir(parents=True)
    submission_zip = submission_dir / "submission.zip"
    submission_zip.write_text("zip")
    public_artifact_root = tmp_path / "PublicArtifacts"

    snapshots = publish_rendered_snapshots(
        score_path=score_path,
        run_dir=run_dir,
        submission_zip=submission_zip,
        public_artifact_root=public_artifact_root,
        public_base_url="http://10.0.0.8:8090",
        profiles=("2k", "4k"),
    )

    assert (public_artifact_root / "2079591" / "2k-rendered.jpg").read_bytes() == payloads_2k["shot_00002.jpg"]
    assert (public_artifact_root / "2079591" / "4k-rendered.jpg").read_bytes() == payloads_4k["shot_00002.png"]
    assert not (submission_dir / "2k-rendered.jpg").exists()
    assert not (submission_dir / "4k-rendered.jpg").exists()
    assert [item["profile"] for item in snapshots] == ["2k", "4k"]


def test_publish_rendered_snapshots_builds_url_from_base_and_numbered_dir(tmp_path):
    run_dir = tmp_path / "results" / "team_20260602"
    run_dir.mkdir(parents=True)
    score_path = run_dir / "score.json"
    _write_score(score_path)
    _write_shots(run_dir, "2k", ["shot_00001.jpg"])
    submission_dir = tmp_path / "RunCode" / "team 2079591"
    submission_dir.mkdir(parents=True)
    submission_zip = submission_dir / "submission.zip"
    submission_zip.write_text("zip")
    public_artifact_root = tmp_path / "PublicArtifacts"

    snapshots = publish_rendered_snapshots(
        score_path=score_path,
        run_dir=run_dir,
        submission_zip=submission_zip,
        public_artifact_root=public_artifact_root,
        public_base_url="http://10.0.0.8:8090/",
        profiles=("2k",),
    )

    assert snapshots == [
        {
            "profile": "2k",
            "label": "2K",
            "filename": "2k-rendered.jpg",
            "url": "http://10.0.0.8:8090/team%202079591/2k-rendered.jpg",
        }
    ]
    score = json.loads(score_path.read_text())
    assert score["rendered_snapshots"] == snapshots


def test_publish_rendered_snapshots_omits_missing_profile(tmp_path):
    run_dir = tmp_path / "results" / "team_20260602"
    run_dir.mkdir(parents=True)
    score_path = run_dir / "score.json"
    _write_score(score_path)
    _write_shots(run_dir, "2k", ["shot_00001.jpg"])
    submission_dir = tmp_path / "RunCode" / "2079591"
    submission_dir.mkdir(parents=True)
    submission_zip = submission_dir / "submission.zip"
    submission_zip.write_text("zip")
    public_artifact_root = tmp_path / "PublicArtifacts"

    snapshots = publish_rendered_snapshots(
        score_path=score_path,
        run_dir=run_dir,
        submission_zip=submission_zip,
        public_artifact_root=public_artifact_root,
        public_base_url="http://10.0.0.8:8090",
        profiles=("2k", "4k"),
    )

    assert (public_artifact_root / "2079591" / "2k-rendered.jpg").exists()
    assert not (public_artifact_root / "2079591" / "4k-rendered.jpg").exists()
    assert [item["profile"] for item in snapshots] == ["2k"]
    score = json.loads(score_path.read_text())
    assert [item["profile"] for item in score["rendered_snapshots"]] == ["2k"]


def test_publish_rendered_snapshots_requires_public_root_for_public_urls(tmp_path):
    run_dir = tmp_path / "results" / "team_20260602"
    run_dir.mkdir(parents=True)
    score_path = run_dir / "score.json"
    _write_score(score_path)
    _write_shots(run_dir, "2k", ["shot_00001.jpg"])
    submission_dir = tmp_path / "RunCode" / "2079591"
    submission_dir.mkdir(parents=True)
    submission_zip = submission_dir / "submission.zip"
    submission_zip.write_text("zip")

    try:
        publish_rendered_snapshots(
            score_path=score_path,
            run_dir=run_dir,
            submission_zip=submission_zip,
            public_artifact_root=None,
            public_base_url="http://10.0.0.8:8090",
            profiles=("2k",),
        )
    except ValueError as exc:
        assert "public artifact root" in str(exc)
    else:
        raise AssertionError("expected missing public artifact root to fail")
