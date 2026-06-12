"""Capability-gated integration test for the loopback egress shaper.

The unit tests in test_contestant_bandwidth_limit.py validate the *command
shape* with fake binaries. This test validates the *actual shaping rate* on the
real `lo` device, which is where the htb burst/cburst calibration for loopback
GSO super-packets matters (the design's primary shaping risk).

It requires CAP_NET_ADMIN (granted on the evaluation host by
scripts/build.sh::apply_net_admin_cap) and is SKIPPED otherwise — so it is a
no-op in unprivileged sandboxes/CI and a real check on the canonical host. It
does not depend on iperf3; a pure-Python loopback transfer measures throughput.
"""

from __future__ import annotations

import socket
import subprocess
import threading
import time

import pytest


FWMARK = "0x64"
LIMIT_MBIT = 50  # shape to 50 Mbit/s for the marked port


def _can_shape_lo() -> bool:
    """True if we can install and remove a root qdisc on lo (have CAP_NET_ADMIN)."""
    add = subprocess.run(
        ["tc", "qdisc", "add", "dev", "lo", "root", "handle", "1:", "htb", "default", "0"],
        capture_output=True,
    )
    if add.returncode != 0:
        return False
    subprocess.run(["tc", "qdisc", "del", "dev", "lo", "root"], capture_output=True)
    return True


pytestmark = pytest.mark.skipif(
    not _can_shape_lo(),
    reason="needs CAP_NET_ADMIN to shape lo (granted on the evaluation host by build.sh)",
)

NFT_TABLE = "evaluator_bw_itest"


def _sh(*args: str) -> None:
    subprocess.run(list(args), check=True, capture_output=True)


def _measure_loopback_mbit(dst_port: int, payload_mb: int = 8) -> float:
    """Push payload_mb MB over a loopback TCP connection to dst_port; return Mbit/s."""
    total = payload_mb * 1024 * 1024
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", dst_port))
    srv.listen(1)

    def _sink() -> None:
        conn, _ = srv.accept()
        got = 0
        while got < total:
            chunk = conn.recv(1024 * 256)
            if not chunk:
                break
            got += len(chunk)
        conn.close()

    t = threading.Thread(target=_sink)
    t.start()
    buf = b"\x00" * (1024 * 256)
    cli = socket.create_connection(("127.0.0.1", dst_port))
    start = time.monotonic()
    sent = 0
    while sent < total:
        sent += cli.send(buf)
    cli.close()
    t.join()
    elapsed = max(time.monotonic() - start, 1e-6)
    srv.close()
    return (total * 8) / elapsed / 1e6


def _teardown() -> None:
    subprocess.run(["tc", "qdisc", "del", "dev", "lo", "root"], capture_output=True)
    subprocess.run(["nft", "delete", "table", "inet", NFT_TABLE], capture_output=True)


def test_marked_loopback_traffic_is_shaped_to_limit():
    marked_port = 53111
    unmarked_port = 53112
    _teardown()  # clean slate
    try:
        # htb root on lo: marked traffic -> 50mbit class, everything else unshaped.
        _sh("tc", "qdisc", "add", "dev", "lo", "root", "handle", "1:", "htb", "default", "0")
        _sh("tc", "class", "add", "dev", "lo", "parent", "1:", "classid", "1:100",
            "htb", "rate", f"{LIMIT_MBIT}mbit", "ceil", f"{LIMIT_MBIT}mbit",
            "burst", "256k", "cburst", "256k")
        _sh("tc", "filter", "add", "dev", "lo", "parent", "1:", "protocol", "all",
            "handle", FWMARK, "fw", "flowid", "1:100")
        # Mark only traffic to marked_port so we can compare shaped vs unshaped.
        _sh("nft", "add", "table", "inet", NFT_TABLE)
        _sh("nft", "add", "chain", "inet", NFT_TABLE, "postrouting",
            "{ type filter hook postrouting priority mangle ; policy accept ; }")
        _sh("nft", "add", "rule", "inet", NFT_TABLE, "postrouting",
            "tcp", "dport", str(marked_port), "meta", "mark", "set", FWMARK)

        shaped = _measure_loopback_mbit(marked_port)
        unshaped = _measure_loopback_mbit(unmarked_port)

        # Marked traffic must be held near the 50mbit cap (allow generous slack
        # for TCP ramp-up and burst); unmarked loopback is far faster.
        assert shaped < LIMIT_MBIT * 1.5, f"marked traffic not shaped: {shaped:.0f} Mbit/s"
        assert unshaped > shaped * 2, (
            f"unmarked traffic ({unshaped:.0f}) should dwarf shaped ({shaped:.0f})"
        )
    finally:
        _teardown()


def test_teardown_leaves_no_residual_shaping_state():
    # Install shaping, tear down, and confirm lo has no htb qdisc and the nft
    # table is gone — a later run must start from a clean slate.
    _teardown()
    _sh("tc", "qdisc", "add", "dev", "lo", "root", "handle", "1:", "htb", "default", "0")
    _sh("nft", "add", "table", "inet", NFT_TABLE)
    _teardown()

    qdisc = subprocess.run(
        ["tc", "qdisc", "show", "dev", "lo"], capture_output=True, text=True
    ).stdout
    assert "htb" not in qdisc, f"residual qdisc on lo: {qdisc!r}"
    ruleset = subprocess.run(
        ["nft", "list", "tables"], capture_output=True, text=True
    ).stdout
    assert NFT_TABLE not in ruleset, f"residual nft table: {ruleset!r}"
