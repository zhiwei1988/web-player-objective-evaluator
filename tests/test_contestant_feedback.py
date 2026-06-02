from __future__ import annotations

import scorer


def test_normalize_contestant_feedback_strips_control_chars_and_limits_output():
    long_line = "x" * (scorer.CONTESTANT_FEEDBACK_MAX_LINE_CHARS + 20)
    out = scorer.normalize_contestant_feedback(
        [
            "  frontend failed\x1b[31m  ",
            "",
            "\tmissing data-testid=player-video\n",
            long_line,
            "line4",
            "line5",
            "line6",
            "line7",
            "line8",
            "line9",
        ]
    )

    assert out[0] == "frontend failed"
    assert out[1] == "missing data-testid=player-video"
    assert len(out) == scorer.CONTESTANT_FEEDBACK_MAX_LINES
    assert len(out[2]) == scorer.CONTESTANT_FEEDBACK_MAX_LINE_CHARS


def test_normalize_contestant_feedback_redacts_absolute_paths():
    out = scorer.normalize_contestant_feedback(
        ["Traceback from /home/zhiwei/workspace/web-player-objective-evaluator/results/team/run.py"]
    )

    assert "/home/zhiwei" not in out[0]
    assert "[path]" in out[0]


def test_build_score_records_sanitized_contestant_feedback_for_contestant_failure():
    out = scorer.build_score(
        {"2k": None, "4k": None},
        chromium_version="test",
        failure_reason="contestant_frontend_unavailable",
        contestant_feedback=[
            " frontend did not become reachable ",
            "/home/zhiwei/workspace/web-player-objective-evaluator/results/run/contestant.log",
        ],
    )

    assert out["contestant_feedback"][0] == "frontend did not become reachable"
    assert "/home/zhiwei" not in out["contestant_feedback"][1]


def test_build_score_records_memory_limit_failure_feedback_and_limit():
    out = scorer.build_score(
        {"2k": None, "4k": None},
        chromium_version="test",
        failure_reason="contestant_memory_limit_exceeded",
        contestant_feedback=["Submission exceeded evaluator memory limit of 10G."],
        contestant_memory_limit="10G",
    )

    assert out["reason"] == "contestant_memory_limit_exceeded"
    assert out["contestant_memory_limit"] == "10G"
    assert out["contestant_feedback"] == [
        "Submission exceeded evaluator memory limit of 10G."
    ]


def test_build_score_records_profile_failure_feedback_without_global_failure():
    out = scorer.build_score(
        {"2k": None, "4k": None},
        chromium_version="test",
        per_round_reasons={"2k": "startup timeout (__PLAYER_ERROR__=decoder failed)"},
        contestant_feedback=["2k startup timeout: decoder failed"],
    )

    assert out["contestant_feedback"] == ["2k startup timeout: decoder failed"]


def test_build_score_does_not_surface_feedback_for_infrastructure_failure():
    out = scorer.build_score(
        {"2k": None, "4k": None},
        chromium_version="test",
        failure_reason="rtsp infrastructure failure (2k unreadable)",
        contestant_feedback=["internal rtsp port 554 failed"],
    )

    assert "contestant_feedback" not in out
