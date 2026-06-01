from __future__ import annotations

import os
import stat
import subprocess
import textwrap
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "_contestant_lifecycle.sh"


def _add_zip_file(zf: zipfile.ZipFile, name: str, content: str, mode: int = 0o644) -> None:
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFREG | mode) << 16
    zf.writestr(info, content)


def _run_extract_harness(tmp_path: Path, submission_zip: Path, team_id: str = "team_zipdir") -> subprocess.CompletedProcess[str]:
    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        ROOT_DIR={str(tmp_path)!r}
        source {str(HELPER)!r}
        clx_prepare_run_dir {team_id!r}
        clx_extract_submission {str(submission_zip)!r}
        printf 'STAGE_DIR=%s\\n' "${{STAGE_DIR}}"
        printf 'RUN_DIR=%s\\n' "${{RUN_DIR}}"
        """
    )
    return subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_extracts_submission_beside_zip_not_under_submissions(tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads" / "team_zipdir"
    upload_dir.mkdir(parents=True)
    submission_zip = upload_dir / "submission.zip"
    with zipfile.ZipFile(submission_zip, "w") as zf:
        _add_zip_file(zf, "start.sh", "#!/usr/bin/env bash\n")
        _add_zip_file(zf, "web/index.html", "<!doctype html>\n")

    proc = _run_extract_harness(tmp_path, submission_zip)

    assert proc.returncode == 0, proc.stderr
    assert (upload_dir / "start.sh").exists()
    assert (upload_dir / "web" / "index.html").exists()
    assert not (tmp_path / "submissions" / "team_zipdir" / "start.sh").exists()
    assert f"STAGE_DIR={upload_dir}" in proc.stdout


def test_lifts_single_top_level_directory_beside_zip(tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads" / "team_lift"
    upload_dir.mkdir(parents=True)
    submission_zip = upload_dir / "submission.zip"
    with zipfile.ZipFile(submission_zip, "w") as zf:
        _add_zip_file(zf, "bundle/start.sh", "#!/usr/bin/env bash\n")
        _add_zip_file(zf, "bundle/web/index.html", "<!doctype html>\n")

    proc = _run_extract_harness(tmp_path, submission_zip, team_id="team_lift")

    assert proc.returncode == 0, proc.stderr
    assert (upload_dir / "start.sh").exists()
    assert (upload_dir / "web" / "index.html").exists()
    assert not (upload_dir / "bundle").exists()
    assert not (tmp_path / "submissions" / "team_lift" / "start.sh").exists()


def test_extracted_files_are_made_executable_recursively(tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads" / "team_exec"
    upload_dir.mkdir(parents=True)
    submission_zip = upload_dir / "submission.zip"
    with zipfile.ZipFile(submission_zip, "w") as zf:
        _add_zip_file(zf, "start.sh", "#!/usr/bin/env bash\n", mode=0o644)
        _add_zip_file(zf, "stop.sh", "#!/usr/bin/env bash\n", mode=0o644)
        _add_zip_file(zf, "bin/helper", "#!/usr/bin/env bash\n", mode=0o644)
        _add_zip_file(zf, "web/index.html", "<!doctype html>\n", mode=0o644)

    proc = _run_extract_harness(tmp_path, submission_zip, team_id="team_exec")

    assert proc.returncode == 0, proc.stderr
    for rel in ["start.sh", "stop.sh", "bin/helper", "web/index.html"]:
        path = upload_dir / rel
        assert path.exists(), rel
        assert os.access(path, os.X_OK), f"{rel} should be executable"


def test_missing_start_records_contestant_feedback(tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads" / "team_missing_start"
    upload_dir.mkdir(parents=True)
    submission_zip = upload_dir / "submission.zip"
    with zipfile.ZipFile(submission_zip, "w") as zf:
        _add_zip_file(zf, "README.txt", "no start script here\n")

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        ROOT_DIR={str(tmp_path)!r}
        source {str(HELPER)!r}
        trap 'printf "FEEDBACK=%s\\n" "${{HOST_CONTESTANT_FEEDBACK[*]:-}}"' EXIT
        clx_prepare_run_dir 'team_missing_start'
        clx_extract_submission {str(submission_zip)!r}
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

    assert proc.returncode == 2
    assert "missing required start.sh" in proc.stdout
