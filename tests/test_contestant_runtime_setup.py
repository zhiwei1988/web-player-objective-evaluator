from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/install_contestant_runtime_ubuntu2204.sh"


def test_contestant_runtime_script_exists_and_is_executable() -> None:
    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK)


def test_contestant_runtime_script_has_valid_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_contestant_runtime_script_reports_supported_versions() -> None:
    text = SCRIPT.read_text()

    assert "SUPPORTED_TOOLS.txt" in text
    for tool in [
        "python3.12",
        "node",
        "pnpm",
        "go",
        "rustc",
        "java",
        "ffmpeg",
        "google-chrome",
        "glibc",
    ]:
        assert tool in text


def test_contestant_runtime_script_reports_libc_version() -> None:
    text = SCRIPT.read_text()

    assert "glibc / libc" in text
    assert "ldd --version" in text


def test_contestant_runtime_script_avoids_corepack_prepare_downloads() -> None:
    text = SCRIPT.read_text()

    assert "corepack prepare" not in text
    assert 'PATH="/usr/bin:/usr/local/bin' in text
    assert "npm_global_install" in text
    assert "pnpm@${PNPM_VERSION}" in text
    assert "yarn@${YARN_VERSION}" in text


def test_contestant_runtime_script_does_not_modify_repo_git_hooks() -> None:
    text = SCRIPT.read_text()

    assert "git lfs install --system --skip-repo" in text
    assert "git lfs update --force" not in text


def test_contestant_runtime_npm_install_has_progress_and_timeout() -> None:
    text = SCRIPT.read_text()

    assert "NPM_INSTALL_TIMEOUT_SECONDS" in text
    assert '"${timeout_cmd[@]}"' in text
    assert "npm ping" in text
    assert "--loglevel=http" in text
    assert "--progress=true" in text


def test_contestant_runtime_npm_install_supports_tls_configuration() -> None:
    text = SCRIPT.read_text()

    assert "NPM_STRICT_SSL" in text
    assert "NPM_CAFILE" in text
    assert "strict-ssl" in text
    assert "cafile" in text
    assert "SELF_SIGNED_CERT_IN_CHAIN" in text
