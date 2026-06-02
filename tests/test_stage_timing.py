from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "_stage_timing.sh"


def _run_harness(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_stage_timeout_budget_defaults_and_env_overrides(tmp_path: Path) -> None:
    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        source {str(HELPER)!r}
        stg_load_timeout_budgets
        printf 'defaults=%s,%s,%s,%s\\n' \
            "${{EVALUATOR_CAPTURE_TIMEOUT_SECONDS}}" \
            "${{EVALUATOR_ANALYSIS_TIMEOUT_SECONDS}}" \
            "${{EVALUATOR_SCORE_TIMEOUT_SECONDS}}" \
            "${{EVALUATOR_TOTAL_TIMEOUT_SECONDS}}"
        EVALUATOR_CAPTURE_TIMEOUT_SECONDS=7 \
        EVALUATOR_ANALYSIS_TIMEOUT_SECONDS=8 \
        EVALUATOR_SCORE_TIMEOUT_SECONDS=9 \
        EVALUATOR_TOTAL_TIMEOUT_SECONDS=10 \
        stg_load_timeout_budgets
        printf 'overrides=%s,%s,%s,%s\\n' \
            "${{EVALUATOR_CAPTURE_TIMEOUT_SECONDS}}" \
            "${{EVALUATOR_ANALYSIS_TIMEOUT_SECONDS}}" \
            "${{EVALUATOR_SCORE_TIMEOUT_SECONDS}}" \
            "${{EVALUATOR_TOTAL_TIMEOUT_SECONDS}}"
        """
    )

    proc = _run_harness(harness, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "defaults=120,240,60,600" in proc.stdout
    assert "overrides=7,8,9,10" in proc.stdout


def test_stage_timing_records_success_failure_timeout_and_skipped(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        source {str(HELPER)!r}
        RUN_DIR={str(run_dir)!r}
        mkdir -p "${{RUN_DIR}}"
        stg_load_timeout_budgets
        stg_init_stage_timings
        stg_run_stage capture 2k 2 bash -c 'exit 0'
        stg_run_stage analysis 2k 2 bash -c 'exit 3'
        stg_run_stage capture 4k 1 bash -c 'sleep 5'
        stg_record_stage skipped analysis 4k 2 "" "no screenshots"
        """
    )

    proc = _run_harness(harness, tmp_path)

    assert proc.returncode == 0, proc.stderr
    data = json.loads((run_dir / "stage_timings.json").read_text())
    assert data["budgets"]["capture_timeout_seconds"] == 120
    records = data["stages"]
    assert [r["status"] for r in records] == [
        "running",
        "success",
        "running",
        "failed",
        "running",
        "timeout",
        "skipped",
    ]
    assert records[0]["stage"] == "capture"
    assert records[0]["profile"] == "2k"
    assert records[3]["exit_code"] == 3
    assert records[5]["reason"] == "capture timeout after 1s"
    assert records[6]["reason"] == "no screenshots"


def test_timeout_stage_does_not_prevent_later_stage_recording(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        source {str(HELPER)!r}
        RUN_DIR={str(run_dir)!r}
        mkdir -p "${{RUN_DIR}}"
        stg_load_timeout_budgets
        stg_init_stage_timings
        stg_run_stage capture 2k 1 bash -c 'sleep 5'
        stg_run_stage capture 4k 2 bash -c 'exit 0'
        """
    )

    proc = _run_harness(harness, tmp_path)

    assert proc.returncode == 0, proc.stderr
    records = json.loads((run_dir / "stage_timings.json").read_text())["stages"]
    assert [r["profile"] for r in records] == ["2k", "2k", "4k", "4k"]
    assert [r["status"] for r in records] == ["running", "timeout", "running", "success"]


def test_stage_timing_records_running_marker_before_command_finishes(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        source {str(HELPER)!r}
        RUN_DIR={str(run_dir)!r}
        mkdir -p "${{RUN_DIR}}"
        stg_load_timeout_budgets
        stg_init_stage_timings
        stg_run_stage capture 2k 5 bash -c 'sleep 1' &
        stage_pid=$!
        sleep 0.2
        python3 - "${{RUN_DIR}}/stage_timings.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1]))
print("mid_statuses=" + ",".join(r["status"] for r in data["stages"]))
print("mid_stage=" + data["stages"][0]["stage"])
print("mid_profile=" + data["stages"][0]["profile"])
PY
        wait "${{stage_pid}}"
        python3 - "${{RUN_DIR}}/stage_timings.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1]))
print("final_statuses=" + ",".join(r["status"] for r in data["stages"]))
PY
        """
    )

    proc = _run_harness(harness, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "mid_statuses=running" in proc.stdout
    assert "mid_stage=capture" in proc.stdout
    assert "mid_profile=2k" in proc.stdout
    assert "final_statuses=running,success" in proc.stdout


def test_stage_record_recreates_missing_stage_timings_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        source {str(HELPER)!r}
        RUN_DIR={str(run_dir)!r}
        mkdir -p "${{RUN_DIR}}"
        stg_load_timeout_budgets
        stg_init_stage_timings
        rm "${{RUN_DIR}}/stage_timings.json"
        stg_run_stage capture 2k 1 bash -c 'exit 0'
        """
    )

    proc = _run_harness(harness, tmp_path)

    assert proc.returncode == 0, proc.stderr
    data = json.loads((run_dir / "stage_timings.json").read_text())
    assert data["budgets"]["capture_timeout_seconds"] == 120
    assert [r["status"] for r in data["stages"]] == ["running", "success"]
