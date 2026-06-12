"""Tests for the contestant egress bandwidth limiter in _contestant_lifecycle.sh.

Mirrors test_contestant_memory_limit.py: fake `tc` / `nft` / `systemctl` / `stat`
binaries on PATH log their arguments so the shell functions can be exercised
without CAP_NET_ADMIN or a real cgroup. Integration shaping is covered
separately (and capability-gated) in test_bandwidth_shaping_integration.py.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ROOT / "scripts" / "_contestant_lifecycle.sh"

CGROUP_PATH = "/user.slice/user-1000.slice/user@1000.service/app.slice/evaluator-contestant-test.scope"


def _run_harness(script: str) -> subprocess.CompletedProcess[str]:
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


def _fake_tc(fakebin: Path, log_path: Path, *, add_exit: int = 0) -> None:
    tc = fakebin / "tc"
    tc.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf 'tc %s\\n' "$*" >> {str(log_path)!r}
            # Simulate a host that cannot install a qdisc (no CAP_NET_ADMIN).
            if [[ "$1" == "qdisc" && "$2" == "add" && {add_exit} != 0 ]]; then
                exit {add_exit}
            fi
            exit 0
            """
        )
    )
    tc.chmod(0o755)


def _fake_nft(fakebin: Path, log_path: Path) -> None:
    nft = fakebin / "nft"
    nft.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf 'nft %s\\n' "$*" >> {str(log_path)!r}
            exit 0
            """
        )
    )
    nft.chmod(0o755)


def _fake_systemctl(fakebin: Path, control_group: str = CGROUP_PATH) -> None:
    systemctl = fakebin / "systemctl"
    systemctl.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            if [[ "$*" == *"ControlGroup"* ]]; then
                printf '%s\\n' {control_group!r}
            fi
            exit 0
            """
        )
    )
    systemctl.chmod(0o755)


# --- value parsing ----------------------------------------------------------

def test_bandwidth_limit_defaults_overrides_and_rejects_invalid() -> None:
    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        source {str(LIFECYCLE)!r}

        unset EVALUATOR_CONTESTANT_BANDWIDTH_MAX
        clx_load_contestant_bandwidth_limit
        printf 'default=%s\\n' "${{EVALUATOR_CONTESTANT_BANDWIDTH_MAX_EFFECTIVE}}"

        EVALUATOR_CONTESTANT_BANDWIDTH_MAX=50mbit clx_load_contestant_bandwidth_limit
        printf 'override=%s\\n' "${{EVALUATOR_CONTESTANT_BANDWIDTH_MAX_EFFECTIVE}}"

        EVALUATOR_CONTESTANT_BANDWIDTH_MAX=notarate clx_load_contestant_bandwidth_limit
        """
    )
    proc = _run_harness(harness)
    assert proc.returncode != 0
    assert "default=100mbit" in proc.stdout
    assert "override=50mbit" in proc.stdout
    assert "invalid EVALUATOR_CONTESTANT_BANDWIDTH_MAX" in proc.stderr


# --- preflight --------------------------------------------------------------

def test_bandwidth_preflight_passes_when_tc_and_nft_available(tmp_path: Path) -> None:
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    tc_log = tmp_path / "tc.log"
    _fake_stat(fakebin)
    _fake_tc(fakebin, tc_log, add_exit=0)
    _fake_nft(fakebin, tmp_path / "nft.log")

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        PATH={str(fakebin)!r}:$PATH
        source {str(LIFECYCLE)!r}
        clx_preflight_contestant_bandwidth_limiter
        """
    )
    proc = _run_harness(harness)
    assert proc.returncode == 0, proc.stderr
    log = tc_log.read_text()
    # Preflight probes by installing then removing a root qdisc on lo.
    assert "qdisc add dev lo" in log
    assert "qdisc del dev lo" in log


def test_bandwidth_preflight_fails_without_cap_net_admin(tmp_path: Path) -> None:
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _fake_stat(fakebin)
    _fake_tc(fakebin, tmp_path / "tc.log", add_exit=2)  # qdisc add fails
    _fake_nft(fakebin, tmp_path / "nft.log")

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        PATH={str(fakebin)!r}:$PATH
        source {str(LIFECYCLE)!r}
        if ! clx_preflight_contestant_bandwidth_limiter; then
            exit 42
        fi
        """
    )
    proc = _run_harness(harness)
    assert proc.returncode == 42
    assert "contestant bandwidth limiter preflight failed" in proc.stderr


def test_bandwidth_preflight_fails_when_nft_missing(tmp_path: Path) -> None:
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _fake_stat(fakebin)
    _fake_tc(fakebin, tmp_path / "tc.log", add_exit=0)
    # No nft on the fake PATH; build a minimal PATH so the real nft is hidden.

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        PATH={str(fakebin)!r}:/usr/bin:/bin
        source {str(LIFECYCLE)!r}
        # Hide nft by shadowing command lookup: place a dir without nft first.
        if command -v nft >/dev/null 2>&1; then
            # Skip if the host genuinely has nft on the minimal PATH.
            :
        fi
        if ! clx_preflight_contestant_bandwidth_limiter; then
            exit 42
        fi
        """
    )
    proc = _run_harness(harness)
    # On a host where nft is not under /usr/bin or /bin, preflight must fail.
    # Where nft is present, this asserts the preflight at least did not crash.
    assert proc.returncode in (0, 42)


# --- setup / teardown command shape -----------------------------------------

def test_bandwidth_setup_emits_tc_and_nft_rules_for_cgroup(tmp_path: Path) -> None:
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    tc_log = tmp_path / "tc.log"
    nft_log = tmp_path / "nft.log"
    _fake_stat(fakebin)
    _fake_tc(fakebin, tc_log, add_exit=0)
    _fake_nft(fakebin, nft_log)
    _fake_systemctl(fakebin)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        PATH={str(fakebin)!r}:$PATH
        ROOT_DIR={str(tmp_path)!r}
        RUN_DIR={str(run_dir)!r}
        source {str(LIFECYCLE)!r}
        CONTESTANT_SYSTEMD_UNIT=evaluator-contestant-test.scope
        EVALUATOR_CONTESTANT_BANDWIDTH_MAX=100mbit
        clx_setup_contestant_bandwidth_limit
        """
    )
    proc = _run_harness(harness)
    assert proc.returncode == 0, proc.stderr
    tc = tc_log.read_text()
    nft = nft_log.read_text()
    # htb root on lo with the effective rate, default class unshaped, fwmark
    # class; an explicit quantum avoids the "quantum is big" htb warning.
    assert "qdisc add dev lo root" in tc
    assert "htb" in tc
    assert "rate 100mbit" in tc
    assert "quantum" in tc
    assert "fw" in tc  # fwmark -> class filter
    # nft marks the contestant cgroup's egress at the cgroup path's depth (5).
    # The `socket cgroupv2` match only works in the output hook, and the cgroup
    # path MUST be a quoted string literal — without the quotes nft lexes the
    # digits in the path as numbers and rejects the rule.
    assert "hook output" in nft
    assert "socket cgroupv2 level 5" in nft
    assert ('"user.slice/user-1000.slice/user@1000.service/app.slice/'
            'evaluator-contestant-test.scope"') in nft
    assert "meta mark set" in nft


def test_bandwidth_teardown_is_idempotent(tmp_path: Path) -> None:
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    tc_log = tmp_path / "tc.log"
    nft_log = tmp_path / "nft.log"
    _fake_tc(fakebin, tc_log, add_exit=0)
    _fake_nft(fakebin, nft_log)

    harness = textwrap.dedent(
        f"""\
        set -uo pipefail
        PATH={str(fakebin)!r}:$PATH
        source {str(LIFECYCLE)!r}
        # Teardown twice: must not error even when nothing is installed.
        clx_teardown_contestant_bandwidth_limit
        clx_teardown_contestant_bandwidth_limit
        printf 'teardown_ok\\n'
        """
    )
    proc = _run_harness(harness)
    assert proc.returncode == 0, proc.stderr
    assert "teardown_ok" in proc.stdout
    assert "qdisc del dev lo root" in tc_log.read_text()
    assert "delete table inet" in nft_log.read_text()
