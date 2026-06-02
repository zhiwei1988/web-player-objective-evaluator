"""Render an internal-only HTML report for a single evaluator run.

All assets are inline (data URIs + inline SVG) so the file opens offline. We
NEVER fetch from a CDN — appeals are reviewed on air-gapped machines.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

from lib.profiles import PROFILES

# Thresholds for "suspicious" gallery picks.
SUSPICIOUS_SSIM = 0.70


def _b64_image(path: Path) -> str | None:
    if not path.exists():
        return None
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _toolchain_fingerprint(run_dir: Path) -> dict:
    """Best-effort: read submodule SHAs from a sibling commit-info file if present."""
    info = {}
    info_file = run_dir / "toolchain.json"
    if info_file.exists():
        try:
            info = json.loads(info_file.read_text())
        except Exception:
            pass
    return info


def _frame_number_svg(frame_numbers: list[int | None], title: str) -> str:
    """Small inline SVG line chart: x = shot index, y = recognized frame number.
    None values produce a gap (no segment drawn)."""
    w, h = 600, 160
    pad = 28
    valid = [(i, fn) for i, fn in enumerate(frame_numbers) if fn is not None]
    if not valid:
        return f'<svg width="{w}" height="{h}"><text x="20" y="80">no frames recognized</text></svg>'
    xs = [v[0] for v in valid]
    ys = [v[1] for v in valid]
    x_min, x_max = 0, max(len(frame_numbers) - 1, 1)
    y_min, y_max = min(ys), max(ys)
    y_span = max(y_max - y_min, 1)
    def sx(x): return pad + (w - 2 * pad) * (x - x_min) / max(x_max - x_min, 1)
    def sy(y): return h - pad - (h - 2 * pad) * (y - y_min) / y_span
    segs = []
    prev = None
    for i, fn in enumerate(frame_numbers):
        if fn is None:
            prev = None
            continue
        if prev is not None:
            segs.append(f'<line x1="{sx(prev[0]):.1f}" y1="{sy(prev[1]):.1f}" '
                        f'x2="{sx(i):.1f}" y2="{sy(fn):.1f}" stroke="#2a6df4" stroke-width="1.5"/>')
        prev = (i, fn)
    body = "".join(segs)
    return f'''
<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" style="background:#fff;border:1px solid #ccc">
<text x="{pad}" y="16" font-size="12" font-family="sans-serif">{html.escape(title)}</text>
<text x="{pad}" y="{h-6}" font-size="10" font-family="sans-serif">shot index →</text>
<text x="6" y="{pad}" font-size="10" font-family="sans-serif">frame #</text>
{body}
</svg>
'''


def _histogram_svg(values: list[float], title: str, bins: int = 20) -> str:
    w, h = 600, 160
    pad = 28
    if not values:
        return f'<svg width="{w}" height="{h}"><text x="20" y="80">no data</text></svg>'
    lo, hi = 0.0, 1.0
    counts = [0] * bins
    for v in values:
        if v < lo:
            v = lo
        if v > hi:
            v = hi
        idx = min(int((v - lo) / (hi - lo) * bins), bins - 1)
        counts[idx] += 1
    cmax = max(counts) or 1
    bar_w = (w - 2 * pad) / bins
    bars = []
    for i, c in enumerate(counts):
        bh = (h - 2 * pad) * (c / cmax)
        x = pad + i * bar_w
        y = h - pad - bh
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w - 1:.1f}" height="{bh:.1f}" fill="#2a6df4"/>')
    return f'''
<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" style="background:#fff;border:1px solid #ccc">
<text x="{pad}" y="16" font-size="12" font-family="sans-serif">{html.escape(title)}</text>
<text x="{pad}" y="{h-6}" font-size="10" font-family="sans-serif">SSIM ∈ [0,1]</text>
{"".join(bars)}
</svg>
'''


def _suspicious_thumbs(profile: str, metrics: dict, run_dir: Path) -> str:
    """Return an HTML block of suspicious screenshots for the profile."""
    shots_dir = run_dir / f"{profile}_screenshots"
    if not shots_dir.exists():
        return f'<p>no {profile} screenshots</p>'
    items = []
    for entry in metrics.get("per_shot", []):
        fn = entry.get("fn")
        ssim_v = entry.get("ssim")
        color_blocks = entry.get("color_blocks")
        suspicious = (
            fn is None
            or (color_blocks is not None and color_blocks < 4)
            or (ssim_v is not None and ssim_v < SUSPICIOUS_SSIM)
        )
        if not suspicious:
            continue
        path = shots_dir / entry["shot"]
        b64 = _b64_image(path)
        if not b64:
            continue
        items.append(
            '<figure style="display:inline-block;margin:6px;text-align:center;font-size:11px">'
            f'<img src="data:image/png;base64,{b64}" style="max-width:200px;border:1px solid #999"/>'
            f'<figcaption>{html.escape(entry["shot"])}<br>'
            f'fn={fn} cb={color_blocks} ssim={ssim_v}</figcaption>'
            '</figure>'
        )
        if len(items) >= 24:
            items.append('<p>(truncated to 24 entries — see metrics JSON for the rest)</p>')
            break
    if not items:
        return f'<p>{profile}: no suspicious screenshots — all checks passed</p>'
    return "".join(items)


def _fmt_metric(value, suffix: str = "") -> str:
    if isinstance(value, (int, float)):
        return f"{value:.2f}{suffix}"
    return "—"


def _capture_diagnostics_table(metrics: dict | None) -> str:
    metrics = metrics or {}
    rows = [
        ("capture sampling fps", _fmt_metric(metrics.get("capture_sampling_fps"))),
        ("capture overrun", _fmt_metric(metrics.get("capture_span_overrun_ratio"), "x")),
        ("repeat frame rate", _fmt_metric(metrics.get("repeat_frame_rate"))),
        ("skipped frame rate", _fmt_metric(metrics.get("dropped_or_skipped_frame_rate"))),
        ("frame progress fps", _fmt_metric(metrics.get("frame_progress_fps"))),
    ]
    body = "".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>"
        for label, value in rows
    )
    return f'''
  <h3>Capture diagnostics</h3>
  <table>
    {body}
  </table>
'''


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _stage_diagnostics_section(run_dir: Path) -> str:
    data = _read_json(run_dir / "stage_timings.json")
    if not data:
        return ""
    budgets = data.get("budgets") if isinstance(data.get("budgets"), dict) else {}
    memory_limit = budgets.get("contestant_memory_max") if isinstance(budgets, dict) else None
    rows = []
    for record in data.get("stages") or []:
        if not isinstance(record, dict):
            continue
        stage = str(record.get("stage") or "")
        profile = str(record.get("profile") or "")
        label = f"{stage}[{profile}]" if profile else stage
        status = str(record.get("status") or "")
        cls = "fail" if status in {"failed", "timeout"} else ("warn" if status in {"skipped", "running"} else "ok")
        timeout = record.get("timeout_seconds")
        timeout_str = f"{timeout}s" if timeout not in (None, "") else ""
        duration = record.get("duration_s")
        duration_str = _fmt_metric(duration, "s")
        reason = str(record.get("reason") or "")
        rows.append(
            "<tr>"
            f"<th>{html.escape(label)}</th>"
            f'<td><span class="{cls}">{html.escape(status)}</span></td>'
            f"<td>{html.escape(timeout_str)}</td>"
            f"<td>{html.escape(duration_str)}</td>"
            f"<td>{html.escape(reason)}</td>"
            "</tr>"
        )
    if not rows and not memory_limit:
        return ""
    memory_limit_html = (
        f'<p>contestant memory limit: <code>{html.escape(str(memory_limit))}</code></p>'
        if memory_limit else ""
    )
    return f'''
<section>
  <h2>Stage diagnostics</h2>
  {memory_limit_html}
  <table>
    <tr><th>stage</th><th>status</th><th>timeout</th><th>duration</th><th>reason</th></tr>
    {''.join(rows)}
  </table>
  <p><a href="stage_timings.json">stage_timings.json</a></p>
</section>
'''


def _read_profile_layout_diagnostics(profile: str, run_dir: Path) -> tuple[dict | None, str]:
    timestamps = _read_json(run_dir / f"{profile}_screenshots" / "timestamps.json")
    layout = (timestamps or {}).get("layout_diagnostics")
    if isinstance(layout, dict):
        return layout, f"{profile}_screenshots/timestamps.json"
    standalone = _read_json(run_dir / f"{profile}_screenshots" / "layout_diagnostics.json")
    if isinstance(standalone, dict):
        return standalone, f"{profile}_screenshots/layout_diagnostics.json"
    return None, f"{profile}_screenshots/timestamps.json"


def _layout_diagnostics_section(profile: str, run_dir: Path) -> str:
    layout, raw_href = _read_profile_layout_diagnostics(profile, run_dir)
    if layout is None:
        return ""
    warnings = [str(w) for w in (layout.get("warnings") or []) if str(w)]
    raw_label = Path(raw_href).name
    if not warnings:
        return f'''
  <h3>Layout diagnostics</h3>
  <p>no layout warnings — <a href="{html.escape(raw_href)}">{html.escape(raw_label)}</a></p>
'''
    items = "".join(f"<li>{html.escape(w)}</li>" for w in warnings[:12])
    if len(warnings) > 12:
        items += f"<li>additional warnings omitted here; see {html.escape(raw_label)}</li>"
    return f'''
  <h3>Layout diagnostics</h3>
  <ul>{items}</ul>
  <p><a href="{html.escape(raw_href)}">{html.escape(raw_label)}</a></p>
'''


def render_report(
    *,
    score: dict,
    profile_metrics: dict[str, dict | None],
    output: Path,
    run_dir: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)

    def profile_block(profile: str, label: str) -> str:
        profile_score = score.get(profile) or {}
        metrics = profile_metrics.get(profile)
        if not profile_score:
            return f'<section><h2>{label}</h2><p>not scored</p></section>'
        # Partial profile_score blocks (failure cases) only carry `reason`; render
        # what we have and bail out of the metrics-dependent rows.
        if "correctness_points" not in profile_score:
            return f'''
<section>
  <h2>{label}</h2>
  <table>
    <tr><th>reason</th><td>{html.escape(profile_score.get("reason") or "")}</td></tr>
  </table>
  {_layout_diagnostics_section(profile, run_dir)}
</section>
'''
        ssim_scores = (metrics or {}).get("ssim_scores", [])
        frame_numbers = (metrics or {}).get("frame_numbers", [])
        expected_fps = profile_score["expected_fps"]
        full_ratio = profile_score.get("fps_full_threshold_used")
        partial_ratio = profile_score.get("fps_partial_threshold_used")
        fps_mode = profile_score.get("fps_scoring_mode")
        if fps_mode == "linear_absolute":
            full_score = profile_score.get("fps_linear_full_score", 5)
            fps_band_row = (
                f"<tr><th>fps formula</th><td>"
                f"linear absolute: min(measured_fps / {expected_fps:g}, 1) × {full_score}"
                f"</td></tr>"
            )
        elif isinstance(full_ratio, (int, float)) and isinstance(partial_ratio, (int, float)):
            fps_band_row = (
                f"<tr><th>fps band</th><td>"
                f"full ≥ {full_ratio:.2f} ({expected_fps * full_ratio:.2f} fps), "
                f"partial ≥ {partial_ratio:.2f} ({expected_fps * partial_ratio:.2f} fps)"
                f"</td></tr>"
            )
        else:
            fps_band_row = ""
        decode_path = profile_score.get("decode_path") or {}
        dp_verdict = decode_path.get("verdict")
        if dp_verdict:
            dp_class = {"violation": "fail", "ok": "ok"}.get(dp_verdict, "warn")
            evidence = decode_path.get("evidence") or []
            ev_str = html.escape(", ".join(str(e) for e in evidence[:8]))
            decode_path_row = (
                f'<tr><th>decode path</th><td><span class="{dp_class}">'
                f'{html.escape(str(dp_verdict))}</span>'
                + (f" — {ev_str}" if ev_str else "")
                + "</td></tr>"
            )
        else:
            decode_path_row = ""
        fps_full_score = profile_score.get("fps_linear_full_score", 5)
        fps_not_scored = (
            " (not scored: level-0 gate failed)"
            if gate and not gate.get("passed", True)
            else ""
        )
        return f'''
<section>
  <h2>{label}</h2>
  <table>
    {decode_path_row}
    <tr><th>correctness</th><td>{profile_score["correctness_points"]}/5</td></tr>
    <tr><th>fps</th><td>{profile_score["fps_points"]}/{fps_full_score}{fps_not_scored}</td></tr>
    <tr><th>measured fps</th><td>{profile_score["measured_fps"]:.2f} (expected {expected_fps})</td></tr>
    {fps_band_row}
    <tr><th>watermark rate</th><td>{profile_score["watermark_recognition_rate"]:.3f}</td></tr>
    <tr><th>color rate</th><td>{profile_score["color_check_rate"]:.3f}</td></tr>
    <tr><th>mean SSIM</th><td>{profile_score["mean_ssim"]:.3f}</td></tr>
    <tr><th>reason</th><td>{html.escape(profile_score.get("reason") or "")}</td></tr>
  </table>
  {_layout_diagnostics_section(profile, run_dir)}
  {_capture_diagnostics_table(metrics or {})}
  {_frame_number_svg(frame_numbers, f"{label}: frame number over time")}
  {_histogram_svg(ssim_scores, f"{label}: SSIM histogram")}
  <h3>Suspicious screenshots</h3>
  {_suspicious_thumbs(profile, metrics or {}, run_dir)}
</section>
'''

    cpu = score.get("cpu") or {}

    def cpu_section() -> str:
        if not cpu:
            return ''
        gated = cpu.get("gated")
        gate_reason = cpu.get("gate_reason") or ""
        mean = cpu.get("mean_percent")
        mean_str = f"{mean:.2f}%" if isinstance(mean, (int, float)) else "—"
        thresholds = cpu.get("thresholds_used") or {}
        thresholds_json = html.escape(json.dumps(thresholds, indent=2))
        gate_class = "fail" if gated else "ok"
        gated_label = (
            f'<span class="{gate_class}">gated: {html.escape(gate_reason)}</span>'
            if gated else '<span class="ok">scored</span>'
        )
        cpu_heading = (
            "not scored (0/5)"
            if gate_reason == "gate_failed"
            else f'{cpu.get("points", 0)}/5'
        )
        # When the gate trips on sampled-profile FPS, surface the exact ratio
        # and cutoff so the contestant understands what throughput would have
        # unlocked CPU.
        gate_callout = ""
        gate_profile = cpu.get("gate_profile") or cpu.get("measured_on_profile") or "2k"
        if gated and gate_reason == f"{gate_profile}_fps_below_threshold":
            gate_ratio = thresholds.get("gate_fps_ratio")
            if isinstance(gate_ratio, (int, float)):
                expected = float(cpu.get("expected_fps") or PROFILES[gate_profile].fps)
                cutoff_fps = expected * gate_ratio
                cutoff_str = f" ({cutoff_fps:.2f} fps against expected {expected:g})"
                gate_callout = (
                    f'<p class="fail"><strong>CPU gate:</strong> requires '
                    f'{html.escape(gate_profile)} measured_fps / expected_fps ≥ {gate_ratio:.2f}{cutoff_str}.</p>'
                )
        return f'''
<section>
  <h2>CPU sub-score: {cpu_heading}</h2>
  {gate_callout}
  <table>
    <tr><th>state</th><td>{gated_label}</td></tr>
    <tr><th>mean CPU</th><td>{mean_str}</td></tr>
    <tr><th>samples</th><td>{cpu.get("sample_count", 0)}</td></tr>
    <tr><th>window</th><td>{cpu.get("sample_window_ms", 0)} ms</td></tr>
    <tr><th>ncpu</th><td>{cpu.get("ncpu")}</td></tr>
    <tr><th>normalization</th><td><code>{html.escape(cpu.get("normalization") or "")}</code></td></tr>
    <tr><th>measured on profile</th><td>{html.escape(cpu.get("measured_on_profile") or "")}</td></tr>
    <tr><th>gate profile</th><td>{html.escape(cpu.get("gate_profile") or "")}</td></tr>
    <tr><th>gate measured fps</th><td>{_fmt_metric(cpu.get("measured_fps"))}</td></tr>
    <tr><th>gate expected fps</th><td>{_fmt_metric(cpu.get("expected_fps"))}</td></tr>
  </table>
  <details><summary>thresholds_used</summary>
    <pre><code>{thresholds_json}</code></pre>
  </details>
</section>
'''

    toolchain = _toolchain_fingerprint(run_dir)
    toolchain_rows = "".join(
        f"<tr><th>{html.escape(k)}</th><td><code>{html.escape(str(v))}</code></td></tr>"
        for k, v in toolchain.items()
    )

    gate = score.get("gate") or {}

    def gate_summary_row() -> str:
        if not gate:
            return ""
        passed = gate.get("passed")
        state = (
            '<span class="ok">passed</span>' if passed
            else '<span class="fail">FAILED</span>'
        )
        return (
            '<tr><th>level-0 gate (decode correctness)</th>'
            f'<td>{state} — 2K correctness {gate.get("2k_correctness_points", 0)}/5, '
            f'4K correctness {gate.get("4k_correctness_points", 0)}/5</td></tr>'
        )

    gate_banner = ""
    if gate and not gate.get("passed", True):
        gate_banner = (
            '<div class="banner" style="background:#fdecea;border-left-color:#c0392b;">'
            '<strong>Level-0 gate failed.</strong> Both profiles were captured, but '
            'level-1 FPS and CPU points are not scored. '
            f'2K correctness {gate.get("2k_correctness_points", 0)}/5, '
            f'4K correctness {gate.get("4k_correctness_points", 0)}/5.</div>'
        )

    profile_labels = {"2k": "2K profile", "4k": "4K profile"}
    summary_rows = []
    if gate:
        summary_rows.append(gate_summary_row())
    contestant_memory_limit = score.get("contestant_memory_limit")
    if not contestant_memory_limit:
        stage_data = _read_json(run_dir / "stage_timings.json") or {}
        budgets = stage_data.get("budgets") if isinstance(stage_data.get("budgets"), dict) else {}
        contestant_memory_limit = (budgets or {}).get("contestant_memory_max")
    if contestant_memory_limit:
        summary_rows.append(
            f'<tr><th>contestant memory limit</th><td><code>{html.escape(str(contestant_memory_limit))}</code></td></tr>'
        )
    artifact_links = []
    for profile in sorted(PROFILES.keys()):
        label = profile_labels.get(profile, f"{profile} profile")
        subtotal = (score.get(profile) or {}).get("total", 0)
        subtotal_max = 15 if profile == "4k" else 10
        summary_rows.append(
            f'<tr><th>{html.escape(label)} subtotal</th><td>{subtotal}/{subtotal_max}</td></tr>'
        )
        artifact_links.extend([
            f'<a href="{profile}_metrics.json">{profile}_metrics.json</a>',
            f'<a href="{profile}_screenshots/">{profile}_screenshots/</a>',
        ])
    cpu_summary_state = (
        " (not scored: level-0 gate failed)"
        if cpu.get("gate_reason") == "gate_failed"
        else (" (gated)" if cpu.get("gated") else "")
    )
    summary_rows.append(
        f'<tr><th>CPU subtotal</th><td>{cpu.get("points", 0)}/5{cpu_summary_state}</td></tr>'
    )
    summary_rows.append(
        f'<tr><th>top-level reason</th><td>{html.escape(score.get("reason") or "")}</td></tr>'
    )
    summary_rows.append(
        f'<tr><th>Chromium</th><td><code>{html.escape(score.get("chromium_version") or "")}</code></td></tr>'
    )

    profile_sections = "\n".join(
        profile_block(profile, profile_labels.get(profile, f"{profile} profile"))
        for profile in sorted(PROFILES.keys())
    )

    review_banner = (
        '<div class="banner" style="background:#fdecea;border-left-color:#c0392b;">'
        "<strong>Review required.</strong> At least one profile's decode path is "
        "inconclusive — forensics could not confirm in-browser H.265 decode. "
        "Manual review advised before publishing this score.</div>"
        if score.get("review_required") else ""
    )

    body = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Evaluator report</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; margin: 24px; color: #222; }}
h1 {{ margin-bottom: 0; }}
h2 {{ border-bottom: 2px solid #2a6df4; padding-bottom: 4px; }}
table {{ border-collapse: collapse; margin: 8px 0 20px; }}
th, td {{ text-align: left; padding: 4px 12px 4px 0; border-bottom: 1px solid #eee; }}
section {{ margin-bottom: 32px; }}
code {{ background: #f5f5f7; padding: 1px 4px; border-radius: 3px; }}
.summary-big {{ font-size: 36px; font-weight: 700; }}
.fail {{ color: #c0392b; }}
.warn {{ color: #d68910; }}
.ok {{ color: #1e8449; }}
.banner {{
  background: #fff3cd; border-left: 4px solid #f1c40f; padding: 12px 16px; margin-bottom: 20px;
}}
</style></head>
<body>
<div class="banner"><strong>Internal use only.</strong> This report is not exposed to contestants.</div>
{gate_banner}
{review_banner}

<h1>Evaluator report</h1>
<p class="summary-big">{score.get("objective_total", 0)} / {score.get("max_score", 30)}</p>

<section>
  <h2>Summary</h2>
  <table>
    {''.join(summary_rows)}
    {toolchain_rows}
  </table>
  <p>
    <a href="score.json">score.json</a> ·
    {' · '.join(artifact_links)} ·
    <a href="evaluator.log">evaluator.log</a>
  </p>
</section>

{profile_sections}
{_stage_diagnostics_section(run_dir)}
{cpu_section()}
</body></html>
'''
    output.write_text(body)
