"""Render an internal-only HTML report for a single evaluator run.

All assets are inline (data URIs + inline SVG) so the file opens offline. We
NEVER fetch from a CDN — appeals are reviewed on air-gapped machines.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

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


def _suspicious_thumbs(codec: str, metrics: dict, run_dir: Path) -> str:
    """Return an HTML block of suspicious screenshots for the codec."""
    shots_dir = run_dir / f"{codec}_screenshots"
    if not shots_dir.exists():
        return f'<p>no {codec} screenshots</p>'
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
        return f'<p>{codec}: no suspicious screenshots — all checks passed</p>'
    return "".join(items)


def render_report(
    *,
    score: dict,
    h264_metrics: dict | None,
    h265_metrics: dict | None,
    output: Path,
    run_dir: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    h264 = score.get("h264") or {}
    h265 = score.get("h265") or {}

    def codec_block(name: str, codec_score: dict, metrics: dict | None) -> str:
        if not codec_score:
            return f'<section><h2>{name}</h2><p>not scored</p></section>'
        # Partial codec_score blocks (failure cases) only carry `reason`; render
        # what we have and bail out of the metrics-dependent rows.
        if "correctness_points" not in codec_score:
            return f'''
<section>
  <h2>{name}</h2>
  <table>
    <tr><th>reason</th><td>{html.escape(codec_score.get("reason") or "")}</td></tr>
  </table>
</section>
'''
        ssim_scores = (metrics or {}).get("ssim_scores", [])
        frame_numbers = (metrics or {}).get("frame_numbers", [])
        return f'''
<section>
  <h2>{name}</h2>
  <table>
    <tr><th>correctness</th><td>{codec_score["correctness_points"]}/10</td></tr>
    <tr><th>fps</th><td>{codec_score["fps_points"]}/5</td></tr>
    <tr><th>measured fps</th><td>{codec_score["measured_fps"]:.2f} (expected {codec_score["expected_fps"]})</td></tr>
    <tr><th>watermark rate</th><td>{codec_score["watermark_recognition_rate"]:.3f}</td></tr>
    <tr><th>color rate</th><td>{codec_score["color_check_rate"]:.3f}</td></tr>
    <tr><th>mean SSIM</th><td>{codec_score["mean_ssim"]:.3f}</td></tr>
    <tr><th>reason</th><td>{html.escape(codec_score.get("reason") or "")}</td></tr>
  </table>
  {_frame_number_svg(frame_numbers, f"{name}: frame number over time")}
  {_histogram_svg(ssim_scores, f"{name}: SSIM histogram")}
  <h3>Suspicious screenshots</h3>
  {_suspicious_thumbs(name.lower(), metrics or {}, run_dir)}
</section>
'''

    toolchain = _toolchain_fingerprint(run_dir)
    toolchain_rows = "".join(
        f"<tr><th>{html.escape(k)}</th><td><code>{html.escape(str(v))}</code></td></tr>"
        for k, v in toolchain.items()
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

<h1>Evaluator report</h1>
<p class="summary-big">{score.get("objective_total", 0)} / {score.get("max_score", 30)}</p>

<section>
  <h2>Summary</h2>
  <table>
    <tr><th>H.264 subtotal</th><td>{h264.get("total", 0)}/15</td></tr>
    <tr><th>H.265 subtotal</th><td>{h265.get("total", 0)}/15</td></tr>
    <tr><th>top-level reason</th><td>{html.escape(score.get("reason") or "")}</td></tr>
    <tr><th>Chromium</th><td><code>{html.escape(score.get("chromium_version") or "")}</code></td></tr>
    {toolchain_rows}
  </table>
  <p>
    <a href="score.json">score.json</a> ·
    <a href="h264_metrics.json">h264_metrics.json</a> ·
    <a href="h265_metrics.json">h265_metrics.json</a> ·
    <a href="evaluator.log">evaluator.log</a> ·
    <a href="h264_screenshots/">h264_screenshots/</a> ·
    <a href="h265_screenshots/">h265_screenshots/</a>
  </p>
</section>

{codec_block("H264", h264, h264_metrics)}
{codec_block("H265", h265, h265_metrics)}
</body></html>
'''
    output.write_text(body)
