from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import textwrap
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "_contestant_lifecycle.sh"


def _can_acquire_lock(lock_file: Path) -> bool:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def _read_pid(path: Path) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for pid file {path}")


def _kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)


def test_contestant_survivor_does_not_inherit_evaluator_lock(tmp_path: Path) -> None:
    lock_file = tmp_path / "evaluator.lock"
    run_dir = tmp_path / "run"
    stage_dir = tmp_path / "stage"
    survivor_pid_file = tmp_path / "contestant-survivor.pid"
    stage_dir.mkdir()
    (stage_dir / "start.sh").write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            sleep 30 &
            echo "$!" > {str(survivor_pid_file)!r}
            exit 0
            """
        )
    )
    (stage_dir / "start.sh").chmod(0o755)

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        ROOT_DIR={str(tmp_path)!r}
        source {str(HELPER)!r}
        LOCK_FILE={str(lock_file)!r}
        RUN_DIR={str(run_dir)!r}
        STAGE_DIR={str(stage_dir)!r}
        mkdir -p "${{RUN_DIR}}"
        clx_acquire_lock
        clx_start_contestant
        for _ in $(seq 1 50); do
            [[ -s {str(survivor_pid_file)!r} ]] && exit 0
            sleep 0.1
        done
        exit 1
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
    survivor_pid = _read_pid(survivor_pid_file)
    try:
        assert proc.returncode == 0, proc.stderr
        assert _can_acquire_lock(lock_file), "surviving contestant descendant retained evaluator lock"
    finally:
        _kill_pid(survivor_pid)


def test_start_contestant_records_new_session_process_group(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    (stage_dir / "start.sh").write_text("#!/usr/bin/env bash\nexec sleep 30\n")
    (stage_dir / "start.sh").chmod(0o755)

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        ROOT_DIR={str(tmp_path)!r}
        source {str(HELPER)!r}
        LOCK_FILE={str(tmp_path / 'evaluator.lock')!r}
        RUN_DIR={str(run_dir)!r}
        STAGE_DIR={str(stage_dir)!r}
        mkdir -p "${{RUN_DIR}}"
        clx_acquire_lock
        clx_start_contestant
        contestant_pid="$(cat "${{RUN_DIR}}/contestant.pid")"
        pgid="$(ps -o pgid= -p "${{contestant_pid}}" | tr -d ' ')"
        kill -TERM -- "-${{contestant_pid}}" 2>/dev/null || true
        [[ "${{pgid}}" == "${{contestant_pid}}" ]]
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


def test_lock_fd_close_helper_prevents_helper_descendant_inheritance(tmp_path: Path) -> None:
    lock_file = tmp_path / "evaluator.lock"
    survivor_pid_file = tmp_path / "helper-survivor.pid"

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        ROOT_DIR={str(tmp_path)!r}
        source {str(HELPER)!r}
        LOCK_FILE={str(lock_file)!r}
        clx_acquire_lock
        clx_without_lock_fd bash -c 'sleep 30 >/dev/null 2>&1 & echo "$!" > {str(survivor_pid_file)!r}'
        for _ in $(seq 1 50); do
            [[ -s {str(survivor_pid_file)!r} ]] && exit 0
            sleep 0.1
        done
        exit 1
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
    survivor_pid = _read_pid(survivor_pid_file)
    try:
        assert proc.returncode == 0, proc.stderr
        assert _can_acquire_lock(lock_file), "surviving helper descendant retained evaluator lock"
    finally:
        _kill_pid(survivor_pid)


def test_concurrent_lock_acquisition_still_fails_while_parent_holds_lock(tmp_path: Path) -> None:
    lock_file = tmp_path / "evaluator.lock"
    ready_file = tmp_path / "holder.ready"
    holder_harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        ROOT_DIR={str(tmp_path)!r}
        source {str(HELPER)!r}
        LOCK_FILE={str(lock_file)!r}
        clx_acquire_lock
        touch {str(ready_file)!r}
        sleep 30
        """
    )
    contender_harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        ROOT_DIR={str(tmp_path)!r}
        source {str(HELPER)!r}
        LOCK_FILE={str(lock_file)!r}
        clx_acquire_lock
        """
    )

    holder = subprocess.Popen(
        ["bash", "-c", holder_harness],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready_file.exists():
            time.sleep(0.05)
        assert ready_file.exists(), "lock holder did not start"
        proc = subprocess.run(
            ["bash", "-c", contender_harness],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    finally:
        holder.terminate()
        try:
            holder.wait(timeout=2)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=2)

    assert proc.returncode == 75
    assert "another evaluator run is in progress" in proc.stderr
