from __future__ import annotations

import subprocess
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ROOT / args[0]), *args[1:]],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_legacy_portable_bundle_files_are_absent() -> None:
    forbidden = [
        "Docker" + "file",
        ".dock" + "erignore",
        "scripts/evaluator-" + "host.sh",
        "scripts/evaluator-" + "local.sh",
        "scripts/package" + ".sh",
        ".play" + "wright",
        "di" + "st",
    ]

    for rel in forbidden:
        assert not (ROOT / rel).exists(), rel


def test_generated_ignore_list_no_longer_mentions_portable_outputs() -> None:
    gitignore = (ROOT / ".gitignore").read_text()

    assert "di" + "st/" not in gitignore
    assert ".play" + "wright/" not in gitignore


def test_evaluator_usage_is_the_single_operator_entrypoint() -> None:
    result = run_script("scripts/evaluator.sh")

    assert result.returncode == 64
    assert "Usage: scripts/evaluator.sh <team_id> <submission_zip>" in result.stderr
    assert "evaluator-" + "local.sh" not in result.stderr
    assert "evaluator-" + "host.sh" not in result.stderr


def test_test_script_rejects_portable_flag_with_usage() -> None:
    result = run_script("scripts/test.sh", "--portable")

    assert result.returncode != 0
    assert "Usage: scripts/test.sh [--only <case>]..." in result.stderr
    assert "--portable" in result.stderr
    assert "removed" in result.stderr
    assert "package" + ".sh" not in result.stderr
    assert "evaluator-" + "host.sh" not in result.stderr


def test_lifecycle_lock_name_matches_single_entrypoint() -> None:
    helper = (ROOT / "scripts/_contestant_lifecycle.sh").read_text()

    assert 'LOCK_FILE="/var/tmp/evaluator.lock"' in helper
    assert "evaluator-" + "host.lock" not in helper


def test_env_allows_repo_imports_from_non_repo_cwd(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f"source {str(ROOT / 'scripts/env.sh')!r} && "
                "python -c 'from lib.profiles import PROFILES; "
                "print(\"\\n\".join(sorted(PROFILES)))'"
            ),
        ],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["2k", "4k"]


def test_shell_entrypoints_use_absolute_venv_python() -> None:
    for rel in [
        "scripts/health_check.sh",
        "scripts/prepare_streams.sh",
        "scripts/deploy.sh",
    ]:
        text = (ROOT / rel).read_text()
        assert re.search(r"(?<!/)\.venv/bin/python", text) is None, rel
