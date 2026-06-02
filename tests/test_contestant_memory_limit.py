from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ROOT / "scripts" / "_contestant_lifecycle.sh"
STAGE_TIMING = ROOT / "scripts" / "_stage_timing.sh"


def _run_harness(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _fake_stat(fakebin: Path, fs_type: str = "cgroup2fs") -> None:
    stat = fakebin / "stat"
    stat.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            if [[ "$*" == "-fc %T /sys/fs/cgroup" ]]; then
                printf '%s\\n' {fs_type!r}
                exit 0
            fi
            exec /usr/bin/stat "$@"
            """
        )
    )
    stat.chmod(0o755)


def _fake_systemd_run(fakebin: Path, log_path: Path, *, exit_code: int = 0) -> None:
    systemd_run = fakebin / "systemd-run"
    systemd_run.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s ' "$@" >> {str(log_path)!r}
            printf '\\n' >> {str(log_path)!r}
            if [[ {exit_code} != 0 ]]; then
                exit {exit_code}
            fi
            while (($#)); do
                case "$1" in
                    --user|--scope|--quiet|--collect|--wait)
                        shift ;;
                    --unit=*|--description=*|--same-dir)
                        shift ;;
                    -p|--property)
                        shift 2 ;;
                    --property=*)
                        shift ;;
                    --)
                        shift
                        break ;;
                    -*)
                        shift ;;
                    *)
                        break ;;
                esac
            done
            exec "$@"
            """
        )
    )
    systemd_run.chmod(0o755)


def _fake_systemctl(fakebin: Path, log_path: Path, show_output: str = "") -> None:
    systemctl = fakebin / "systemctl"
    systemctl.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s ' "$@" >> {str(log_path)!r}
            printf '\\n' >> {str(log_path)!r}
            if [[ "$*" == *" show "* || "$1" == "show" || "$2" == "show" ]]; then
                printf '%s\\n' {show_output!r}
            fi
            exit 0
            """
        )
    )
    systemctl.chmod(0o755)


def test_memory_limit_defaults_overrides_invalid_values_and_stage_metadata(tmp_path: Path) -> None:
    run_default = tmp_path / "default"
    run_override = tmp_path / "override"
    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        source {str(LIFECYCLE)!r}
        source {str(STAGE_TIMING)!r}

        ROOT_DIR={str(tmp_path)!r}
        RUN_DIR={str(run_default)!r}
        mkdir -p "${{RUN_DIR}}"
        unset EVALUATOR_CONTESTANT_MEMORY_MAX
        clx_load_contestant_memory_limit
        printf 'default=%s\\n' "${{EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE}}"
        stg_load_timeout_budgets
        stg_init_stage_timings

        RUN_DIR={str(run_override)!r}
        mkdir -p "${{RUN_DIR}}"
        EVALUATOR_CONTESTANT_MEMORY_MAX=512m clx_load_contestant_memory_limit
        printf 'override=%s\\n' "${{EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE}}"
        stg_init_stage_timings

        EVALUATOR_CONTESTANT_MEMORY_MAX=0 clx_load_contestant_memory_limit
        """
    )

    proc = _run_harness(harness, tmp_path)

    assert proc.returncode != 0
    assert "default=10G" in proc.stdout
    assert "override=512M" in proc.stdout
    assert "invalid EVALUATOR_CONTESTANT_MEMORY_MAX" in proc.stderr
    default_data = json.loads((run_default / "stage_timings.json").read_text())
    override_data = json.loads((run_override / "stage_timings.json").read_text())
    assert default_data["budgets"]["contestant_memory_max"] == "10G"
    assert override_data["budgets"]["contestant_memory_max"] == "512M"


def test_memory_limiter_preflight_success_and_failure_blocks_start(tmp_path: Path) -> None:
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    run_log = tmp_path / "systemd-run.log"
    _fake_stat(fakebin)
    _fake_systemd_run(fakebin, run_log, exit_code=77)
    marker = tmp_path / "start.invoked"
    stage_dir = tmp_path / "stage"
    run_dir = tmp_path / "run"
    stage_dir.mkdir()
    (stage_dir / "start.sh").write_text(f"#!/usr/bin/env bash\ntouch {str(marker)!r}\n")
    (stage_dir / "start.sh").chmod(0o755)

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        PATH={str(fakebin)!r}:$PATH
        ROOT_DIR={str(tmp_path)!r}
        RUN_DIR={str(run_dir)!r}
        STAGE_DIR={str(stage_dir)!r}
        mkdir -p "${{RUN_DIR}}"
        source {str(LIFECYCLE)!r}
        clx_load_contestant_memory_limit
        if ! clx_preflight_contestant_memory_limiter; then
            exit 42
        fi
        clx_start_contestant
        """
    )

    proc = _run_harness(harness, tmp_path)

    assert proc.returncode == 42
    assert "contestant memory limiter preflight failed" in proc.stderr
    assert not marker.exists(), "contestant start.sh must not run when preflight fails"
    assert "MemoryMax=10G" in run_log.read_text()


def test_memory_limited_launch_preserves_pgid_log_and_records_unit(tmp_path: Path) -> None:
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    run_log = tmp_path / "systemd-run.log"
    ctl_log = tmp_path / "systemctl.log"
    _fake_stat(fakebin)
    _fake_systemd_run(fakebin, run_log)
    _fake_systemctl(fakebin, ctl_log)

    stage_dir = tmp_path / "stage"
    run_dir = tmp_path / "run"
    stage_dir.mkdir()
    (stage_dir / "start.sh").write_text(
        "#!/usr/bin/env bash\n"
        "echo contestant-started\n"
        "exec sleep 30\n"
    )
    (stage_dir / "start.sh").chmod(0o755)

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        PATH={str(fakebin)!r}:$PATH
        ROOT_DIR={str(tmp_path)!r}
        RUN_DIR={str(run_dir)!r}
        STAGE_DIR={str(stage_dir)!r}
        RESULTS_SUBDIR=team_mem_20260602_120000
        mkdir -p "${{RUN_DIR}}"
        source {str(LIFECYCLE)!r}
        EVALUATOR_CONTESTANT_MEMORY_MAX=256M
        clx_load_contestant_memory_limit
        clx_start_contestant
        contestant_pid="$(cat "${{RUN_DIR}}/contestant.pid")"
        pgid="$(ps -o pgid= -p "${{contestant_pid}}" | tr -d ' ')"
        printf 'pid=%s pgid=%s unit=%s\\n' "${{contestant_pid}}" "${{pgid}}" "$(cat "${{RUN_DIR}}/contestant.systemd_unit")"
        grep -q contestant-started "${{RUN_DIR}}/contestant.log"
        [[ "${{contestant_pid}}" == "${{pgid}}" ]]
        clx_cleanup_contestant
        """
    )

    proc = _run_harness(harness, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "unit=evaluator-contestant-team_mem_20260602_120000.scope" in proc.stdout
    systemd_args = run_log.read_text()
    assert "MemoryAccounting=yes" in systemd_args
    assert "MemoryMax=256M" in systemd_args
    assert "MemorySwapMax=0" in systemd_args
    assert "KillMode=control-group" in systemd_args
    systemctl_args = ctl_log.read_text()
    assert "kill" in systemctl_args
    assert "stop" in systemctl_args


def test_memory_limit_oom_state_is_detected_from_systemd_unit(tmp_path: Path) -> None:
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    ctl_log = tmp_path / "systemctl.log"
    _fake_systemctl(fakebin, ctl_log, show_output="Result=oom-kill\nOOMKilled=yes\nExecMainStatus=137")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "contestant.systemd_unit").write_text("evaluator-contestant-test.scope\n")

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        PATH={str(fakebin)!r}:$PATH
        ROOT_DIR={str(tmp_path)!r}
        RUN_DIR={str(run_dir)!r}
        source {str(LIFECYCLE)!r}
        clx_contestant_memory_limit_exceeded
        """
    )

    proc = _run_harness(harness, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "evaluator-contestant-test.scope" in ctl_log.read_text()
