"""Publish contestant-visible rendered snapshots and record their URLs.

This module is intentionally small: it copies one representative capture per
profile into the public artifact root, then records public URLs in score.json
for result_info.py to render.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote


LogFn = Callable[[str], None]


def _default_profiles() -> tuple[str, ...]:
    from lib.profiles import PROFILES

    return tuple(sorted(PROFILES.keys()))


def _profile_label(profile: str) -> str:
    return profile.upper()


def _middle_capture(shots_dir: Path) -> Path | None:
    shots = sorted([*shots_dir.glob("shot_*.jpg"), *shots_dir.glob("shot_*.png")])
    if not shots:
        return None
    return shots[len(shots) // 2]


def _snapshot_url(public_base_url: str, numbered_dir: str, filename: str) -> str:
    base = public_base_url.rstrip("/")
    return f"{base}/{quote(numbered_dir)}/{quote(filename)}"


def _write_score_snapshot_metadata(score_path: Path, snapshots: list[dict[str, str]]) -> None:
    score = json.loads(score_path.read_text())
    if snapshots:
        score["rendered_snapshots"] = snapshots
    else:
        score.pop("rendered_snapshots", None)
    tmp = score_path.with_suffix(score_path.suffix + ".tmp")
    tmp.write_text(json.dumps(score, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    tmp.replace(score_path)


def publish_rendered_snapshots(
    *,
    score_path: Path,
    run_dir: Path,
    submission_zip: Path,
    public_artifact_root: Path | None,
    public_base_url: str | None,
    profiles: Iterable[str] | None = None,
    log: LogFn | None = None,
) -> list[dict[str, str]]:
    """Copy fixed-middle profile screenshots and persist public link metadata.

    Missing source screenshots are not fatal; they simply omit that profile's
    snapshot. A missing publication directory or copy/write failure is fatal.
    """
    log = log or (lambda _msg: None)
    profile_list = tuple(profiles) if profiles is not None else _default_profiles()
    submission_dir = submission_zip.parent
    if not submission_dir.is_dir():
        raise FileNotFoundError(f"submission zip dirname does not exist: {submission_dir}")

    snapshots: list[dict[str, str]] = []
    numbered_dir = submission_dir.name
    base_url = public_base_url.strip() if public_base_url else ""
    artifact_root = public_artifact_root if public_artifact_root and str(public_artifact_root).strip() else None
    if base_url and artifact_root is None:
        raise ValueError("public artifact root is required when public base URL is configured")
    if artifact_root is None:
        log("public artifact root not configured; rendered snapshots are not published")
        return snapshots

    publish_dir = artifact_root / numbered_dir
    publish_dir.mkdir(parents=True, exist_ok=True)

    for profile in profile_list:
        source = _middle_capture(run_dir / f"{profile}_screenshots")
        if source is None:
            log(f"no rendered snapshot source for profile {profile}")
            continue

        filename = f"{profile}-rendered.jpg"
        dest = publish_dir / filename
        shutil.copyfile(source, dest)
        log(f"rendered snapshot published: {dest}")

        if base_url:
            snapshots.append({
                "profile": profile,
                "label": _profile_label(profile),
                "filename": filename,
                "url": _snapshot_url(base_url, numbered_dir, filename),
            })

    if base_url:
        _write_score_snapshot_metadata(score_path, snapshots)
    return snapshots


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Publish rendered profile snapshots and update score.json.",
    )
    parser.add_argument("--score-json", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--submission-zip", type=Path, required=True)
    parser.add_argument("--public-artifact-root", type=Path, default=None)
    parser.add_argument("--public-base-url", default="")
    parser.add_argument("--profile", action="append", default=[],
                        help="Profile key to publish. Defaults to lib.profiles.PROFILES.")
    args = parser.parse_args()

    def log(msg: str) -> None:
        print(f"[render_snapshot_publication] {msg}", file=sys.stderr)

    profiles = tuple(args.profile) if args.profile else None
    publish_rendered_snapshots(
        score_path=args.score_json,
        run_dir=args.run_dir,
        submission_zip=args.submission_zip,
        public_artifact_root=args.public_artifact_root,
        public_base_url=args.public_base_url,
        profiles=profiles,
        log=log,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
