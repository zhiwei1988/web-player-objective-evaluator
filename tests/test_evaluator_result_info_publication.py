"""Shell-level tests proving result.info publication target.

Exercises scripts/_result_info_lifecycle.sh::write_result_info — the helper
sourced by scripts/evaluator.sh — against a controlled RUN_DIR and a
synthetic submission-zip parent directory. The contract is:

- audit copy lands at ${RUN_DIR}/result.info
- contestant-platform copy lands at $(dirname "${SUBMISSION_ZIP}")/result.info
- a legacy submissions/<team_id>/ directory is NOT a publication target

Heavy pipeline pieces (mediamtx, ffmpeg, Chromium) are intentionally out of
scope; this test only proves the publication contract.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "_result_info_lifecycle.sh"
ENV_SH = ROOT / "scripts" / "env.sh"


def _full_score_json() -> dict:
    return {
        "max_score": 30,
        "objective_total": 19.5,
        "cpu": {
            "points": 3,
            "mean_percent": 6.5,
            "sample_count": 25,
            "sample_window_ms": 25000,
            "ncpu": 8,
            "normalization": "all_cores_total",
            "measured_on_profile": "2k",
            "gate_profile": "2k",
            "expected_fps": 20.0,
            "measured_fps": 20.0,
            "thresholds_used": {
                "gate_fps_ratio": 0.65, "full_percent": 5.0,
                "partial_start_percent": 6.0, "zero_percent": 20.0,
                "min_samples": 3, "sample_hz": 1.0,
            },
            "gated": False,
            "gate_reason": None,
        },
        "chromium_version": "Chromium 131.0.6778.85",
        "2k": {
            "correctness_points": 5, "fps_points": 5, "total": 10,
            "measured_fps": 20.0, "expected_fps": 20.0,
            "watermark_recognition_rate": 1.0, "color_check_rate": 1.0,
            "mean_ssim": 0.95,
        },
        "4k": {
            "correctness_points": 5, "fps_points": 1.5, "total": 6.5,
            "measured_fps": 6.5, "expected_fps": 20.0,
            "watermark_recognition_rate": 0.97, "color_check_rate": 0.98,
            "mean_ssim": 0.89,
        },
    }


def _failure_score_json(reason: str, contestant_feedback: list[str] | None = None) -> dict:
    score = {
        "max_score": 30,
        "objective_total": 0,
        "cpu": {
            "points": 0,
            "mean_percent": None,
            "sample_count": 0,
            "sample_window_ms": 0,
            "ncpu": None,
            "normalization": "all_cores_total",
            "measured_on_profile": "2k",
            "gate_profile": "2k",
            "expected_fps": 20.0,
            "measured_fps": 0.0,
            "thresholds_used": {
                "gate_fps_ratio": 0.65, "full_percent": 5.0,
                "partial_start_percent": 6.0, "zero_percent": 20.0,
                "min_samples": 3, "sample_hz": 0.0,
            },
            "gated": True,
            "gate_reason": "host_failure",
        },
        "chromium_version": "Chromium 131.0.6778.85",
        "2k": None,
        "4k": None,
        "reason": reason,
    }
    if contestant_feedback is not None:
        score["contestant_feedback"] = contestant_feedback
    return score


def _drive_write_result_info(
    *,
    tmp_path: Path,
    score: dict,
    failure_reason: str | None,
    result_code_arg: str = "",
    team_id: str = "team_ref",
    public_artifact_base_url: str | None = None,
    public_artifact_root: Path | None = None,
    screenshot_profiles: tuple[str, ...] = (),
    submission_dir_name: str = "uploads",
) -> tuple[Path, Path, Path, subprocess.CompletedProcess[str]]:
    """Stage RUN_DIR + submission zip parent + score.json, then drive
    write_result_info via a small bash harness that sources the helper.

    Returns (run_dir, submission_dir, legacy_submissions_dir, completed_process).
    """
    run_dir = tmp_path / "results" / f"{team_id}_20260522_120000"
    run_dir.mkdir(parents=True)
    (run_dir / "score.json").write_text(json.dumps(score))
    for profile in screenshot_profiles:
        shots_dir = run_dir / f"{profile}_screenshots"
        shots_dir.mkdir()
        for idx in range(3):
            (shots_dir / f"shot_{idx:05d}.jpg").write_bytes(
                f"{profile}:shot_{idx:05d}".encode("ascii")
            )

    uploads_dir = tmp_path / submission_dir_name
    uploads_dir.mkdir()
    submission_zip = uploads_dir / f"{team_id}.zip"
    submission_zip.write_text("not a real zip")

    # The contract forbids publishing into legacy submissions/<team_id>/; the
    # test creates that directory so we can later confirm it was NOT written to.
    submissions_dir = tmp_path / "submissions" / team_id
    submissions_dir.mkdir(parents=True)

    harness = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -uo pipefail
        ROOT_DIR={str(ROOT)!r}
        RUN_DIR={str(run_dir)!r}
        SCORE_FILE="${{RUN_DIR}}/score.json"
        RESULT_INFO_FILE="${{RUN_DIR}}/result.info"
        SUBMISSION_ZIP={str(submission_zip)!r}
        FAILURE_REASON={(failure_reason or "")!r}
        HOST_FAILURE_REASON=""
        EVALUATOR_PUBLIC_ARTIFACT_BASE_URL={(public_artifact_base_url or "")!r}
        EVALUATOR_PUBLIC_ARTIFACT_ROOT={(str(public_artifact_root) if public_artifact_root else "")!r}
        export EVALUATOR_PUBLIC_ARTIFACT_BASE_URL
        export EVALUATOR_PUBLIC_ARTIFACT_ROOT
        RUN_START_NS="$(date +%s%N)"
        # Drop a small sleep so |runtime| is a non-zero positive integer
        # — this protects the assertion that runtime is recorded.
        sleep 0.01

        # Tame the renderer's logger.
        log() {{ printf '[harness] %s\\n' "$*"; }}

        source {str(ENV_SH)!r}
        source {str(HELPER)!r}

        if [[ -n {result_code_arg!r} ]]; then
            write_result_info {result_code_arg}
        else
            write_result_info
        fi
    """)
    proc = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return run_dir, uploads_dir, submissions_dir, proc


# ---------------------------------------------------------------------------
# Successful run: result.info is published to dirname(submission_zip), NOT
# to legacy submissions/<team_id>/.
# ---------------------------------------------------------------------------

def test_successful_run_publishes_result_info_next_to_submission_zip(tmp_path):
    run_dir, uploads_dir, submissions_dir, proc = _drive_write_result_info(
        tmp_path=tmp_path,
        score=_full_score_json(),
        failure_reason=None,
        result_code_arg="0",
    )

    assert proc.returncode == 0, proc.stderr

    audit = run_dir / "result.info"
    published = uploads_dir / "result.info"
    forbidden = submissions_dir / "result.info"

    assert audit.exists(), "audit copy must be written under RUN_DIR"
    assert published.exists(), "must be published next to the submission zip"
    assert not forbidden.exists(), (
        "publication target must NOT be submissions/<team_id>/result.info"
    )

    assert audit.read_bytes() == published.read_bytes(), (
        "audit and published copies must be byte-identical"
    )

    content = audit.read_text()
    assert "|result|0" in content
    assert "|score|19.5" in content
    assert "Objective Score: 19.5 / 30" in content
    assert "- CPU: 3 / 5" in content


def test_successful_run_publishes_rendered_snapshots_next_to_result_info(tmp_path):
    public_artifact_root = tmp_path / "public-artifacts"
    run_dir, uploads_dir, _, proc = _drive_write_result_info(
        tmp_path=tmp_path,
        score=_full_score_json(),
        failure_reason=None,
        result_code_arg="0",
        team_id="2079591",
        public_artifact_base_url="http://10.0.0.8:8090",
        public_artifact_root=public_artifact_root,
        screenshot_profiles=("2k", "4k"),
        submission_dir_name="2079591",
    )

    assert proc.returncode == 0, proc.stderr

    assert (public_artifact_root / "2079591" / "2k-rendered.jpg").read_bytes() == b"2k:shot_00001"
    assert (public_artifact_root / "2079591" / "4k-rendered.jpg").read_bytes() == b"4k:shot_00001"
    assert not (uploads_dir / "2k-rendered.jpg").exists()
    assert not (uploads_dir / "4k-rendered.jpg").exists()

    score = json.loads((run_dir / "score.json").read_text())
    assert score["rendered_snapshots"] == [
        {
            "profile": "2k",
            "label": "2K",
            "filename": "2k-rendered.jpg",
            "url": "http://10.0.0.8:8090/2079591/2k-rendered.jpg",
        },
        {
            "profile": "4k",
            "label": "4K",
            "filename": "4k-rendered.jpg",
            "url": "http://10.0.0.8:8090/2079591/4k-rendered.jpg",
        },
    ]

    info = (uploads_dir / "result.info").read_text().split("|debug|")[0]
    assert "Rendered Snapshots:" in info
    assert "- 2K: http://10.0.0.8:8090/2079591/2k-rendered.jpg" in info
    assert "- 4K: http://10.0.0.8:8090/2079591/4k-rendered.jpg" in info


def test_missing_profile_screenshot_omits_only_that_snapshot(tmp_path):
    public_artifact_root = tmp_path / "public-artifacts"
    run_dir, uploads_dir, _, proc = _drive_write_result_info(
        tmp_path=tmp_path,
        score=_full_score_json(),
        failure_reason=None,
        result_code_arg="0",
        team_id="2079591",
        public_artifact_base_url="http://10.0.0.8:8090",
        public_artifact_root=public_artifact_root,
        screenshot_profiles=("2k",),
        submission_dir_name="2079591",
    )

    assert proc.returncode == 0, proc.stderr
    assert (uploads_dir / "result.info").exists()
    assert (public_artifact_root / "2079591" / "2k-rendered.jpg").exists()
    assert not (public_artifact_root / "2079591" / "4k-rendered.jpg").exists()
    assert not (uploads_dir / "2k-rendered.jpg").exists()

    score = json.loads((run_dir / "score.json").read_text())
    assert [item["profile"] for item in score["rendered_snapshots"]] == ["2k"]

    info = (uploads_dir / "result.info").read_text().split("|debug|")[0]
    assert "- 2K: http://10.0.0.8:8090/2079591/2k-rendered.jpg" in info
    assert "4k-rendered.jpg" not in info


def test_runtime_is_recorded_as_nonnegative_integer_milliseconds(tmp_path):
    run_dir, _, _, proc = _drive_write_result_info(
        tmp_path=tmp_path,
        score=_full_score_json(),
        failure_reason=None,
        result_code_arg="0",
    )
    assert proc.returncode == 0, proc.stderr

    text = (run_dir / "result.info").read_text()
    runtime_line = next(ln for ln in text.split("\n") if ln.startswith("|runtime|"))
    runtime_value = runtime_line.split("|", 2)[2]
    assert runtime_value.isdigit(), runtime_line
    assert int(runtime_value) >= 0


# ---------------------------------------------------------------------------
# Contestant-side failure: same publication contract, result=0 inferred.
# ---------------------------------------------------------------------------

def test_contestant_side_failure_publishes_with_result_zero(tmp_path):
    run_dir, uploads_dir, submissions_dir, proc = _drive_write_result_info(
        tmp_path=tmp_path,
        score=_failure_score_json("contestant_frontend_unavailable"),
        failure_reason="contestant_frontend_unavailable",
        result_code_arg="",  # let classify_result_code infer from FAILURE_REASON
    )

    assert proc.returncode == 0, proc.stderr

    audit = run_dir / "result.info"
    published = uploads_dir / "result.info"
    forbidden = submissions_dir / "result.info"

    assert audit.exists()
    assert published.exists()
    assert not forbidden.exists()

    content = audit.read_text()
    assert "|result|0" in content
    assert "|score|0" in content
    assert "Objective Score: 0 / 30" in content
    assert "- 2K Correctness: 0 / 5" in content
    assert "- CPU: 0 / 5" in content
    # Raw reason stays out of the contestant-visible info block.
    assert "contestant_frontend_unavailable" not in content.split("|debug|")[0]
    # And appears in debug.
    assert "contestant_frontend_unavailable" in content.split("|debug|")[1]


def test_memory_limit_failure_publishes_with_result_zero_and_feedback(tmp_path):
    run_dir, uploads_dir, _, proc = _drive_write_result_info(
        tmp_path=tmp_path,
        score={
            **_failure_score_json(
                "contestant_memory_limit_exceeded",
                contestant_feedback=["Submission exceeded evaluator memory limit of 10G."],
            ),
            "contestant_memory_limit": "10G",
        },
        failure_reason="contestant_memory_limit_exceeded",
        result_code_arg="",
    )

    assert proc.returncode == 0, proc.stderr
    content = (run_dir / "result.info").read_text()
    assert (uploads_dir / "result.info").read_text() == content
    assert "|result|0" in content
    assert "Submission exceeded evaluator memory limit of 10G." in content
    assert "contestant_memory_limit=10G" in content


def test_contestant_side_feedback_is_published_identically(tmp_path):
    run_dir, uploads_dir, _, proc = _drive_write_result_info(
        tmp_path=tmp_path,
        score=_failure_score_json(
            "contestant_frontend_unavailable",
            contestant_feedback=[
                "Frontend did not become reachable.",
                "npm ERR! missing script: start",
            ],
        ),
        failure_reason="contestant_frontend_unavailable",
        result_code_arg="",
    )

    assert proc.returncode == 0, proc.stderr

    audit = run_dir / "result.info"
    published = uploads_dir / "result.info"
    assert audit.read_bytes() == published.read_bytes()

    content = audit.read_text()
    assert "Execution Feedback:" in content
    assert "- Frontend did not become reachable." in content
    assert "- npm ERR! missing script: start" in content


# ---------------------------------------------------------------------------
# Infrastructure failure: result=1, still published to uploads/.
# ---------------------------------------------------------------------------

def test_infrastructure_failure_publishes_with_result_one(tmp_path):
    reason = "rtsp infrastructure failure (2k unreadable)"
    run_dir, uploads_dir, submissions_dir, proc = _drive_write_result_info(
        tmp_path=tmp_path,
        score=_failure_score_json(reason),
        failure_reason=reason,
        result_code_arg="",  # inference path: unknown reason → 1
    )

    assert proc.returncode == 0, proc.stderr

    audit = run_dir / "result.info"
    published = uploads_dir / "result.info"
    forbidden = submissions_dir / "result.info"

    assert audit.exists()
    assert published.exists()
    assert not forbidden.exists()

    content = audit.read_text()
    assert "|result|1" in content
    assert "|score|0" in content
    assert reason in content.split("|debug|")[1]


# ---------------------------------------------------------------------------
# Publication failure is treated as an evaluator-side failure.
# ---------------------------------------------------------------------------

def test_missing_submission_zip_parent_returns_failure(tmp_path):
    run_dir = tmp_path / "results" / "team_ref_20260522_120000"
    run_dir.mkdir(parents=True)
    (run_dir / "score.json").write_text(json.dumps(_full_score_json()))

    # SUBMISSION_ZIP's dirname intentionally does not exist.
    submission_zip = tmp_path / "no" / "such" / "dir" / "team_ref.zip"

    harness = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -uo pipefail
        ROOT_DIR={str(ROOT)!r}
        RUN_DIR={str(run_dir)!r}
        SCORE_FILE="${{RUN_DIR}}/score.json"
        RESULT_INFO_FILE="${{RUN_DIR}}/result.info"
        SUBMISSION_ZIP={str(submission_zip)!r}
        FAILURE_REASON=""
        HOST_FAILURE_REASON=""
        RUN_START_NS="$(date +%s%N)"
        log() {{ printf '[harness] %s\\n' "$*"; }}
        source {str(ENV_SH)!r}
        source {str(HELPER)!r}
        write_result_info 0
    """)
    proc = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode != 0, (
        "missing publication directory must surface as a non-zero exit so the "
        "evaluator can flag it as an operator-actionable failure"
    )
    combined = proc.stdout + proc.stderr
    assert "does not exist" in combined or "publish result.info" in combined


# ---------------------------------------------------------------------------
# Static guards: evaluator.sh must source the helper and never target the
# legacy submissions/<team_id>/ path for result.info publication.
# ---------------------------------------------------------------------------

def test_evaluator_sh_sources_the_result_info_helper():
    text = (ROOT / "scripts" / "evaluator.sh").read_text()
    assert "_result_info_lifecycle.sh" in text


def test_evaluator_sh_never_publishes_into_legacy_submissions_dir():
    text = (ROOT / "scripts" / "evaluator.sh").read_text()
    forbidden = "submissions/${TEAM_ID}/result.info"
    assert forbidden not in text, (
        "result.info must publish to $(dirname \"$SUBMISSION_ZIP\")/result.info."
    )


def test_result_info_helper_uses_submission_zip_dirname():
    text = HELPER.read_text()
    assert 'dirname "${SUBMISSION_ZIP}"' in text
