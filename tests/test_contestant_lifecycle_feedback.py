from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "_contestant_lifecycle.sh"


def test_startup_log_feedback_can_flow_into_score_json(tmp_path: Path) -> None:
    log_file = tmp_path / "contestant.log"
    log_file.write_text(
        "\n".join(
            [
                "line that should be skipped",
                "Starting submitted frontend",
                "npm ERR! missing script: start",
                "See /home/zhiwei/workspace/web-player-objective-evaluator/results/team/log",
            ]
        )
    )
    score_file = tmp_path / "score.json"

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        ROOT_DIR={str(ROOT)!r}
        source {str(HELPER)!r}
        clx_collect_contestant_log_feedback 'Frontend did not become reachable.' {str(log_file)!r} 3
        args=()
        for line in "${{HOST_CONTESTANT_FEEDBACK[@]}}"; do
            args+=(--contestant-feedback "$line")
        done
        "${{ROOT_DIR}}/.venv/bin/python" "${{ROOT_DIR}}/scorer.py" \
            --output {str(score_file)!r} \
            --failure-reason contestant_frontend_unavailable \
            "${{args[@]}}"
        """
    )
    proc = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    score = json.loads(score_file.read_text())
    feedback = "\n".join(score["contestant_feedback"])
    assert "Frontend did not become reachable." in feedback
    assert "npm ERR! missing script: start" in feedback
    assert "/home/zhiwei" not in feedback
