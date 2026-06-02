"""Render the contestant-facing `result.info` from `score.json` + run metadata.

The contest platform expects a line-oriented file with fields
`|result|`, `|score|`, `|runtime|`, `|info|`, `|debug|` in that order. The
`|info|` marker stands alone on its line; everything between it and the
`|debug|` marker is the contestant-visible breakdown. `|debug|` begins the
final multi-line organizer-facing field.

`result.info` is a projection of `score.json` (the authoritative score
artifact) and is intentionally lossy:

- `info` is contestant-visible: total score, the five scoring item point
  values (2K correctness, 2K FPS, 4K correctness, 4K FPS, CPU), and optional
  sanitized execution feedback for contestant-side failures.
- `debug` is organizer-facing: run directory, top-level reason, per-profile
  measured diagnostics, CPU gate diagnostics, and Chromium version.

Format spec lives in `openspec/specs/evaluator/spec.md` under
"Contest Platform Result Info". The sample is `reference/result-sample.info`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


# (label, profile_key, points_key, max_points). profile_key=None marks the
# CPU sub-score, which lives under score["cpu"]["points"].
_SCORE_ITEMS: tuple[tuple[str, str | None, str | None, int], ...] = (
    ("2K Correctness", "2k", "correctness_points", 5),
    ("2K FPS",         "2k", "fps_points",         5),
    ("4K Correctness", "4k", "correctness_points", 5),
    ("4K FPS",         "4k", "fps_points",         10),
    ("CPU",            None, None,                 5),
)


# Contestant-side failure reasons that still produce a trustworthy
# `result=0` zero-score outcome. Anything else is treated as
# infrastructure/evaluator failure and rendered with `result=1`.
CONTESTANT_SIDE_FAILURE_REASONS: frozenset[str] = frozenset({
    "contestant_frontend_unavailable",
    "contestant_memory_limit_exceeded",
})


def _fmt_num(value: Any) -> str:
    """Format a number without unnecessary trailing zeroes.

    None and non-numeric values render as "0" — the contract is that the
    five scoring items always appear with concrete numbers.
    """
    if value is None:
        return "0"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def classify_result_code(reason: str | None) -> int:
    """Return 0 for contestant-side outcomes, 1 for infra/evaluator failures.

    A missing reason means a normal completion → 0.
    """
    if not reason:
        return 0
    return 0 if reason in CONTESTANT_SIDE_FAILURE_REASONS else 1


def _build_info_lines(score: dict) -> list[str]:
    objective_total = score.get("objective_total", 0)
    max_score = score.get("max_score", 30)
    lines = [
        f"Objective Score: {_fmt_num(objective_total)} / {_fmt_num(max_score)}",
        "Breakdown:",
    ]
    for label, profile_key, points_key, max_pts in _SCORE_ITEMS:
        if profile_key is None:
            pts = (score.get("cpu") or {}).get("points")
        else:
            block = score.get(profile_key) or {}
            pts = block.get(points_key)
        if pts is None:
            pts = 0
        lines.append(f"- {label}: {_fmt_num(pts)} / {_fmt_num(max_pts)}")

    info_metrics: list[str] = []
    score_2k = score.get("2k") or {}
    score_4k = score.get("4k") or {}
    score_cpu = score.get("cpu") or {}
    if score_2k.get("measured_fps") is not None:
        info_metrics.append(f"2k: measured_fps={_fmt_num(score_2k['measured_fps'])}")
    if score_4k.get("measured_fps") is not None:
        info_metrics.append(f"4k: measured_fps={_fmt_num(score_4k['measured_fps'])}")
    if score_cpu.get("mean_percent") is not None:
        info_metrics.append(f"cpu: mean_percent={_fmt_num(score_cpu['mean_percent'])}")
    if info_metrics:
        lines.extend(["", "Runtime Metrics:"])
        lines.extend(info_metrics)

    snapshot_lines: list[str] = []
    snapshots = score.get("rendered_snapshots")
    if isinstance(snapshots, list):
        for item in snapshots:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            label = str(item.get("label") or item.get("profile") or "Snapshot").strip()
            snapshot_lines.append(f"- {label}: {url}")
    if snapshot_lines:
        lines.extend(["", "Rendered Snapshots:"])
        lines.extend(snapshot_lines)

    feedback = score.get("contestant_feedback")
    if isinstance(feedback, str):
        feedback_lines = [feedback.strip()] if feedback.strip() else []
    elif isinstance(feedback, list):
        feedback_lines = [
            str(item).strip()
            for item in feedback
            if item is not None and str(item).strip()
        ]
    else:
        feedback_lines = []
    if feedback_lines:
        lines.extend(["", "Execution Feedback:"])
        lines.extend(f"- {line}" for line in feedback_lines)
    return lines


def _build_debug_lines(score: dict, run_dir: str | Path | None) -> list[str]:
    lines: list[str] = []
    if run_dir:
        lines.append(f"run_dir={run_dir}")
    chromium = score.get("chromium_version")
    if chromium:
        lines.append(f"chromium={chromium}")
    reason = score.get("reason")
    if reason:
        lines.append(f"reason={reason}")
    contestant_memory_limit = score.get("contestant_memory_limit")
    if contestant_memory_limit:
        lines.append(f"contestant_memory_limit={contestant_memory_limit}")

    for profile_key in ("2k", "4k"):
        block = score.get(profile_key)
        if not block:
            continue
        parts: list[str] = []
        if "measured_fps" in block and block["measured_fps"] is not None:
            parts.append(f"measured_fps={_fmt_num(block['measured_fps'])}")
        if "expected_fps" in block and block["expected_fps"] is not None:
            parts.append(f"expected_fps={_fmt_num(block['expected_fps'])}")
        if "watermark_recognition_rate" in block and block["watermark_recognition_rate"] is not None:
            parts.append(f"watermark={_fmt_num(block['watermark_recognition_rate'])}")
        if "color_check_rate" in block and block["color_check_rate"] is not None:
            parts.append(f"color={_fmt_num(block['color_check_rate'])}")
        if "mean_ssim" in block and block["mean_ssim"] is not None:
            parts.append(f"ssim={_fmt_num(block['mean_ssim'])}")
        per_round_reason = block.get("reason")
        if per_round_reason:
            parts.append(f"reason={per_round_reason}")
        if parts:
            lines.append(f"{profile_key}: " + " ".join(parts))

    cpu = score.get("cpu") or {}
    if cpu:
        parts = []
        if cpu.get("mean_percent") is not None:
            parts.append(f"mean_percent={_fmt_num(cpu['mean_percent'])}")
        if cpu.get("sample_count") is not None:
            parts.append(f"sample_count={cpu['sample_count']}")
        if cpu.get("measured_fps") is not None:
            parts.append(f"measured_fps={_fmt_num(cpu['measured_fps'])}")
        if cpu.get("gate_reason"):
            parts.append(f"gate_reason={cpu['gate_reason']}")
        if parts:
            lines.append("cpu: " + " ".join(parts))
    return lines


def render(
    score: dict,
    runtime_ms: int,
    run_dir: str | Path | None = None,
    result_code: int = 0,
) -> str:
    """Render `result.info` content from a score dict and run metadata.

    Args:
        score: contents of `score.json`.
        runtime_ms: evaluator wall-clock runtime in milliseconds.
        run_dir: path to the per-run directory (debug-only).
        result_code: 0 for a trustworthy contestant outcome (including valid
            zero-score outcomes caused by the contestant submission); 1 for
            evaluator/host/infrastructure failures.

    The returned string ends with a trailing newline.
    """
    info_lines = _build_info_lines(score)
    debug_lines = _build_debug_lines(score, run_dir)

    objective_total = score.get("objective_total", 0)

    out: list[str] = [
        f"|result|{int(result_code)}",
        f"|score|{_fmt_num(objective_total)}",
        f"|runtime|{int(runtime_ms)}",
        "|info|",
        *info_lines,
    ]
    if debug_lines:
        out.append(f"|debug|{debug_lines[0]}")
        out.extend(debug_lines[1:])
    else:
        out.append("|debug|")
    return "\n".join(out) + "\n"


def _cli() -> int:
    p = argparse.ArgumentParser(
        description="Render result.info from score.json + run metadata.",
    )
    p.add_argument("--score-json", type=Path, required=True,
                   help="Path to score.json produced by scorer.py.")
    p.add_argument("--runtime-ms", type=int, required=True,
                   help="Evaluator wall-clock runtime in milliseconds.")
    p.add_argument("--run-dir", type=Path, required=False, default=None,
                   help="Run directory; used in the debug field.")
    p.add_argument("--output", type=Path, required=True,
                   help="Path to write result.info.")
    p.add_argument("--result-code", type=int, choices=(0, 1), default=None,
                   help="Override the result code. Defaults to inference "
                        "from score.json.reason: known contestant-side "
                        "reasons → 0, otherwise → 1; missing reason → 0.")
    args = p.parse_args()

    score = json.loads(args.score_json.read_text())

    if args.result_code is None:
        result_code = classify_result_code(score.get("reason"))
    else:
        result_code = args.result_code

    text = render(
        score=score,
        runtime_ms=args.runtime_ms,
        run_dir=str(args.run_dir) if args.run_dir else None,
        result_code=result_code,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
