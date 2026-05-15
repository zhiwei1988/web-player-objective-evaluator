# switch-to-h265-resolution-profiles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the evaluator from codec rounds (H.264 + H.265) to resolution profiles (2K + 4K, both H.265), unify the RTSP endpoint at `:554`, redesign the contestant frontend URL to `?profile=2k|4k`, and rebalance scoring to `2K 10 + 4K 10 + CPU 10 = 30`.

**Architecture:** Centralize all per-round parameters in a new `lib/profiles.py` registry (`ProfileSpec` dataclass + `PROFILES` map). Every pipeline module, script, and MediaMTX path derives from this registry. The legacy `codec` axis disappears; the new `profile` axis is the single dimension. CPU sub-score sampling and gating both move from "H.265 round" to "4K profile" via a `cpu_sampled` flag on the spec.

**Tech Stack:** Python 3.12 (Playwright 1.49, Pillow 10.4, NumPy 1.26, scikit-image 0.24, pylibdmtx, pytesseract), source-built ffmpeg/MediaMTX/tesseract/leptonica/libdmtx/x264/x265, MediaMTX RTSP on port 554, Docker (ubuntu:24.04) for the portable bundle, pytest for unit tests, `scripts/test.sh` for integration.

**Dependency order:**

```
1 (profiles)  →  2 (watermark) + 4 (RTSP)  →  3 (streams)
                       ↓
              5 (pipeline modules)
                       ↓
              6 (orchestration scripts)
                       ↓
              7 (Dockerfile) + 8 (packaging)
                       ↓
              9 (test fixtures)
                       ↓
              10 (self-test execution)
                       ↓
              11 (docs) + 12 (cleanup, optional)
                       ↓
              13 (final verification)
```

Tasks 1, 2, 4 may run in parallel after Task 1 lands; everything else is sequential.

---

## Task 1: Profile abstraction foundation

**Files:**
- Create: `lib/profiles.py`
- Create: `tests/test_profiles.py`

- [ ] **Step 1: Write the failing test for `PROFILES` registry shape**

Create `tests/test_profiles.py`:

```python
"""Sanity test for lib.profiles — the single truth source for evaluator
per-round parameters."""

from __future__ import annotations

from lib.profiles import PROFILES, ProfileSpec, RTSP_PORT, FRONTEND_PORT, rtsp_url


def test_profiles_has_2k_and_4k():
    assert set(PROFILES.keys()) == {"2k", "4k"}


def test_profile_2k_values():
    spec = PROFILES["2k"]
    assert isinstance(spec, ProfileSpec)
    assert spec.name == "2k"
    assert spec.width == 2560
    assert spec.height == 1440
    assert spec.fps == 25
    assert spec.bitrate == "4M"
    assert spec.rtsp_path == "test/h265_2560_1440"
    assert spec.stream_file == "streams/h265_2560_1440.mp4"
    assert spec.reference_dir == "reference/2k"
    assert spec.duration_s == 30
    assert spec.cpu_sampled is False


def test_profile_4k_values():
    spec = PROFILES["4k"]
    assert spec.width == 3840
    assert spec.height == 2160
    assert spec.fps == 25
    assert spec.bitrate == "8M"
    assert spec.rtsp_path == "test/h265_3840_2160"
    assert spec.stream_file == "streams/h265_3840_2160.mp4"
    assert spec.reference_dir == "reference/4k"
    assert spec.duration_s == 30
    assert spec.cpu_sampled is True


def test_only_4k_is_cpu_sampled():
    sampled = [name for name, s in PROFILES.items() if s.cpu_sampled]
    assert sampled == ["4k"]


def test_rtsp_constants():
    assert RTSP_PORT == 554
    assert FRONTEND_PORT == 8080


def test_rtsp_url_helper():
    assert rtsp_url("2k") == "rtsp://127.0.0.1:554/test/h265_2560_1440"
    assert rtsp_url("4k") == "rtsp://127.0.0.1:554/test/h265_3840_2160"
    assert rtsp_url("2k", host="10.0.0.1") == "rtsp://10.0.0.1:554/test/h265_2560_1440"
```

- [ ] **Step 2: Run the test and confirm it fails**

```bash
.venv/bin/python -m pytest tests/test_profiles.py -v
```

Expected: `ModuleNotFoundError: No module named 'lib.profiles'` (or similar).

- [ ] **Step 3: Create `lib/profiles.py`**

```python
"""Single truth source for evaluator per-round parameters.

Every pipeline module (runner.py, analyzer.py, scorer.py, report.py),
every orchestration script (scripts/evaluator.sh, scripts/prepare_streams.sh,
scripts/health_check.sh), and the RTSP server config (rtsp_server/mediamtx.yml)
SHALL derive their per-round constants from PROFILES below — not from local
literals. Adding a new resolution profile is a one-spec addition here plus
matching MediaMTX path + reference stream regeneration.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileSpec:
    name: str                    # "2k" | "4k"
    width: int
    height: int
    fps: int
    bitrate: str                 # libx265 -b:v parameter, e.g. "4M" / "8M"
    rtsp_path: str               # MediaMTX path, e.g. "test/h265_2560_1440"
    stream_file: str             # path relative to repo root
    reference_dir: str           # path relative to repo root
    duration_s: int = 30
    cpu_sampled: bool = False    # True → runner.py starts CPU sampler in this round


RTSP_PORT = 554
FRONTEND_PORT = 8080


PROFILES: dict[str, ProfileSpec] = {
    "2k": ProfileSpec(
        name="2k",
        width=2560,
        height=1440,
        fps=25,
        bitrate="4M",
        rtsp_path="test/h265_2560_1440",
        stream_file="streams/h265_2560_1440.mp4",
        reference_dir="reference/2k",
    ),
    "4k": ProfileSpec(
        name="4k",
        width=3840,
        height=2160,
        fps=25,
        bitrate="8M",
        rtsp_path="test/h265_3840_2160",
        stream_file="streams/h265_3840_2160.mp4",
        reference_dir="reference/4k",
        cpu_sampled=True,
    ),
}


def rtsp_url(profile: str, host: str = "127.0.0.1") -> str:
    """Compose the canonical RTSP URL for a profile.

    Profile name is looked up in PROFILES; passing an unknown name raises KeyError.
    """
    return f"rtsp://{host}:{RTSP_PORT}/{PROFILES[profile].rtsp_path}"
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
.venv/bin/python -m pytest tests/test_profiles.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add lib/profiles.py tests/test_profiles.py
git commit -m "feat(profiles): introduce ProfileSpec registry as single truth source"
```

---

## Task 2: Watermark generator CLI migration

**Files:**
- Modify: `lib/watermark.py` (`_cli` function and docstring)

This task is independent of Task 4 (RTSP) and can run in parallel.

- [ ] **Step 1: Update `lib/watermark.py::_cli()` to accept `--profile`**

Edit `lib/watermark.py`, replace the existing `_cli()` function with:

```python
def _cli() -> int:
    from lib.profiles import PROFILES

    p = argparse.ArgumentParser(description="Generate watermarked reference frames.")
    p.add_argument("--profile", required=True, choices=sorted(PROFILES.keys()),
                   help="Resolution profile key from lib.profiles.PROFILES.")
    p.add_argument("--width", type=int, default=None,
                   help="Override profile width (debug only).")
    p.add_argument("--height", type=int, default=None,
                   help="Override profile height (debug only).")
    p.add_argument("--fps", type=float, default=None,
                   help="Override profile fps (debug only).")
    p.add_argument("--duration", type=float, default=None,
                   help="Override profile duration in seconds (debug only).")
    p.add_argument("--out", type=Path, default=None,
                   help="Override output directory (default: profile's reference_dir).")
    args = p.parse_args()

    spec = PROFILES[args.profile]
    width = args.width or spec.width
    height = args.height or spec.height
    fps = args.fps or spec.fps
    duration = args.duration or spec.duration_s
    out_dir = args.out or Path(spec.reference_dir)

    n = write_sequence(out_dir, width, height, fps, duration)
    print(f"wrote {n} frames to {out_dir}")
    return 0
```

- [ ] **Step 2: Update `WatermarkLayout` docstring**

In `lib/watermark.py`, find the `WatermarkLayout` dataclass and replace the line:

```
the frame dimensions so a single layout works for both 1920x1080 and
2560x1440.
```

with:

```
the frame dimensions so a single layout works for all profile resolutions
(currently 2560x1440 and 3840x2160; was 1920x1080 H.264 before this change).
```

- [ ] **Step 3: Smoke-test the new CLI emits the expected frame count**

```bash
rm -rf /tmp/wm_test
.venv/bin/python -m lib.watermark --profile 2k --duration 1 --out /tmp/wm_test
ls /tmp/wm_test | wc -l
```

Expected: 25 PNG files (1 second × 25 fps).

- [ ] **Step 4: Smoke-test the 4K path renders without erroring**

```bash
rm -rf /tmp/wm_test_4k
.venv/bin/python -m lib.watermark --profile 4k --duration 1 --out /tmp/wm_test_4k
ls /tmp/wm_test_4k | wc -l
file /tmp/wm_test_4k/frame_00000.png
```

Expected: 25 PNG files; `file` reports 3840 x 2160 8-bit/color RGB.

- [ ] **Step 5: Spot-check 4K watermark readability**

```bash
.venv/bin/python -c "
from PIL import Image
from pylibdmtx.pylibdmtx import decode
img = Image.open('/tmp/wm_test_4k/frame_00012.png').convert('RGB')
w, h = img.size
side = int(min(w, h) * 0.18)
crop = img.crop((w - side, int(h * 0.78), w, h))
res = decode(crop, max_count=1, timeout=2000)
print('decoded:', res[0].data.decode() if res else 'NONE')"
```

Expected: `decoded: 12`.

- [ ] **Step 6: Commit Task 2**

```bash
git add lib/watermark.py
git commit -m "refactor(watermark): switch CLI from --codec to --profile"
```

---

## Task 3: Stream preparation pipeline

**Files:**
- Modify: `scripts/prepare_streams.sh` (full rewrite of the codec-specific blocks)

This task depends on Task 1 (profiles) and Task 2 (watermark CLI).

- [ ] **Step 1: Rewrite `scripts/prepare_streams.sh` body**

Replace `scripts/prepare_streams.sh` with:

```bash
#!/usr/bin/env bash
# Generate the two watermarked H.265 reference streams used by the evaluator.
# Per-profile parameters live in lib/profiles.py::PROFILES — this script
# iterates that registry, never hard-codes resolutions/fps/bitrates.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

log()  { printf '[prepare_streams] %s\n' "$*" >&2; }
die()  { printf 'prepare_streams failed: %s\n' "$*" >&2; exit 1; }

command -v ffmpeg >/dev/null   || die "ffmpeg not on PATH (run scripts/build.sh)"
command -v ffprobe >/dev/null  || die "ffprobe not on PATH (run scripts/build.sh)"

cd "${ROOT_DIR}"

# Remove legacy codec-dimensioned assets so two layouts cannot coexist.
log "removing legacy H.264 / H.265-by-codec assets if present"
rm -rf streams/h264_watermarked.mp4 streams/h265_watermarked.mp4 reference/h264 reference/h265

# Read PROFILES via python so the registry stays the single source.
PROFILE_KEYS=$(.venv/bin/python -c "from lib.profiles import PROFILES; print(' '.join(sorted(PROFILES.keys())))")

mkdir -p streams

for profile in ${PROFILE_KEYS}; do
    eval "$(.venv/bin/python <<PY
from lib.profiles import PROFILES
s = PROFILES["${profile}"]
print(f"PW={s.width}; PH={s.height}; PFPS={s.fps}; PBR='{s.bitrate}'; "
      f"PDUR={s.duration_s}; PREF='{s.reference_dir}'; PMP4='{s.stream_file}'")
PY
)"
    PGOP=$(( PFPS * 2 ))
    PBUFSIZE="$(python3 -c "print(int('${PBR}'.rstrip('M')) * 2)")M"

    log "generating profile=${profile} reference PNGs (${PW}x${PH}@${PFPS}, ${PDUR}s)"
    .venv/bin/python -m lib.watermark \
        --profile "${profile}" --out "${PREF}"

    log "encoding profile=${profile} MP4 (libx265 ${PBR}, GOP=${PGOP})"
    ffmpeg -y -loglevel error \
        -framerate "${PFPS}" \
        -i "${PREF}/frame_%05d.png" \
        -c:v libx265 -tag:v hvc1 -b:v "${PBR}" -maxrate "${PBR}" -bufsize "${PBUFSIZE}" -pix_fmt yuv420p \
        -x265-params "keyint=${PGOP}:min-keyint=${PGOP}:scenecut=0" \
        -movflags +faststart \
        "${PMP4}"

    # Sanity: ffprobe confirms resolution.
    fields="$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=width,height,r_frame_rate \
        -of default=nw=1:nk=1 "${PMP4}")"
    w="$(printf '%s\n' "${fields}" | sed -n '1p')"
    h="$(printf '%s\n' "${fields}" | sed -n '2p')"
    [[ "${w}" == "${PW}" && "${h}" == "${PH}" ]] \
        || die "profile=${profile}: expected ${PW}x${PH}, got ${w}x${h} in ${PMP4}"

    # Spot-check DataMatrix on first / middle / last frame.
    total=$(( PFPS * PDUR ))
    for sample in 0 $((total / 2)) $((total - 1)); do
        png="$(printf '%s/frame_%05d.png' "${PREF}" "${sample}")"
        got="$(.venv/bin/python -c "
from pylibdmtx.pylibdmtx import decode
from PIL import Image
img = Image.open('${png}').convert('RGB')
w, h = img.size
side = int(min(w, h) * 0.18)
crop = img.crop((w - side, int(h * 0.78), w, h))
res = decode(crop, max_count=1, timeout=2000)
print(res[0].data.decode() if res else 'NONE')")"
        if [[ "${got}" != "${sample}" ]]; then
            die "spot check profile=${profile}: frame ${sample} decoded as '${got}', expected '${sample}'"
        fi
    done
    log "  profile=${profile} spot checks OK"
done

printf 'prepare_streams ok\n'
```

- [ ] **Step 2: Run prepare_streams.sh end-to-end**

```bash
./scripts/prepare_streams.sh
```

Expected output ends with `prepare_streams ok`. `ls streams/` shows `h265_2560_1440.mp4` and `h265_3840_2160.mp4` only (no h264/old h265 files).

- [ ] **Step 3: Verify legacy assets were removed**

```bash
ls streams/h264_watermarked.mp4 streams/h265_watermarked.mp4 reference/h264 reference/h265 2>&1
```

Expected: each path reports "No such file or directory".

- [ ] **Step 4: Inspect disk footprint of the new 4K artifacts**

```bash
du -sh reference/2k reference/4k streams/h265_2560_1440.mp4 streams/h265_3840_2160.mp4
```

Expected: 4K reference dir ~250 MB (750 frames × ~300 KB), 4K mp4 ~30 MB. Accept and move on if disk has room.

- [ ] **Step 5: Commit Task 3**

```bash
git add scripts/prepare_streams.sh
git commit -m "refactor(prepare_streams): drive from lib.profiles, drop H.264 path"
```

---

## Task 4: MediaMTX RTSP server (port 554 + capability binding)

**Files:**
- Modify: `rtsp_server/mediamtx.yml`
- Modify: `scripts/build.sh` (add setcap step)
- Modify: `scripts/setup.sh` (add `libcap2-bin` to apt list)
- Modify: `scripts/health_check.sh`
- Modify: `scripts/start_rtsp.sh` (only if it contains the `8554` literal)
- Modify: `scripts/env.sh`

May run in parallel with Task 2 after Task 1 lands.

- [ ] **Step 1: Replace `rtsp_server/mediamtx.yml`**

```yaml
# MediaMTX configuration for the objective evaluator.
# Listens on :554 only; forces RTSP-over-TCP for both client and contestant traffic.
# The two paths use ffmpeg's on-demand publish mode: when a reader connects,
# MediaMTX spawns ffmpeg to loop the pre-encoded MP4 with -c:v copy (no re-encode).
#
# Port 554 is below 1024 and requires CAP_NET_BIND_SERVICE on the mediamtx
# binary (set by scripts/build.sh on the build host) or --cap-add=NET_BIND_SERVICE
# on the docker run (set by scripts/evaluator-host.sh in the container path).
#
# IMPORTANT: MediaMTX does NOT expand env vars or templates in YAML string values.
# The `-i streams/...` path is RELATIVE; scripts/start_rtsp.sh `cd`s into the repo
# root before launching MediaMTX so that ffmpeg's inherited cwd resolves it.

logLevel: warn
logDestinations: [stdout]

# Disable everything except RTSP-TCP.
rtsp: yes
protocols: [tcp]
rtspAddress: :554
rtmp: no
hls: no
webrtc: no
srt: no
api: no
metrics: no
pprof: no
playback: no

paths:
  test/h265_2560_1440:
    runOnDemand: >-
      ffmpeg -hide_banner -loglevel warning
      -re -stream_loop -1
      -i streams/h265_2560_1440.mp4
      -c:v copy -an -f rtsp
      -rtsp_transport tcp
      rtsp://127.0.0.1:554/test/h265_2560_1440
    runOnDemandRestart: yes
    runOnDemandCloseAfter: 10s

  test/h265_3840_2160:
    runOnDemand: >-
      ffmpeg -hide_banner -loglevel warning
      -re -stream_loop -1
      -i streams/h265_3840_2160.mp4
      -c:v copy -an -f rtsp
      -rtsp_transport tcp
      rtsp://127.0.0.1:554/test/h265_3840_2160
    runOnDemandRestart: yes
    runOnDemandCloseAfter: 10s
```

- [ ] **Step 2: Add `libcap2-bin` to `scripts/setup.sh` apt list**

Find the apt install command in `scripts/setup.sh` and append `libcap2-bin` to the package list. Example before/after fragment:

```
build-essential cmake autoconf automake libtool pkg-config nasm yasm \
golang-go python3-venv lsof unzip
```

becomes

```
build-essential cmake autoconf automake libtool pkg-config nasm yasm \
golang-go python3-venv lsof unzip libcap2-bin
```

- [ ] **Step 3: Add the setcap step to `scripts/build.sh`**

After the block that builds/installs MediaMTX (search for `mediamtx` in the script), insert:

```bash
# Grant CAP_NET_BIND_SERVICE so mediamtx can bind :554 without root.
# The container path achieves the same via --cap-add=NET_BIND_SERVICE.
log "applying setcap cap_net_bind_service to mediamtx"
if ! sudo setcap cap_net_bind_service=+ep "${THIRD_PARTY_INSTALL}/bin/mediamtx"; then
    printf 'build.sh: setcap on mediamtx failed; cannot bind :554 natively\n' >&2
    printf 'build.sh: install libcap2-bin and confirm sudo is available, then retry\n' >&2
    exit 1
fi
getcap "${THIRD_PARTY_INSTALL}/bin/mediamtx"
```

(Adjust the `THIRD_PARTY_INSTALL` variable name to match what the script uses; commonly it's exported by `env.sh`.)

- [ ] **Step 4: Update `scripts/health_check.sh` to probe the new paths**

Replace the body of `scripts/health_check.sh` (which currently takes a `h264|h265` codec argument) with a profile-based equivalent. Suggested rewrite:

```bash
#!/usr/bin/env bash
# Probe one (or all) MediaMTX RTSP paths with ffprobe.
# Usage: health_check.sh <profile> [timeout_seconds]
#        health_check.sh                  # all profiles in PROFILES

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SCRIPT_DIR}/env.sh"

if (( $# >= 1 )); then
    PROFILES_TO_CHECK=("$1")
    TIMEOUT="${2:-15}"
else
    mapfile -t PROFILES_TO_CHECK < <(.venv/bin/python -c \
        "from lib.profiles import PROFILES; print('\n'.join(sorted(PROFILES.keys())))")
    TIMEOUT=15
fi

failed=0
for profile in "${PROFILES_TO_CHECK[@]}"; do
    url="$(.venv/bin/python -c "from lib.profiles import rtsp_url; print(rtsp_url('${profile}'))")"
    printf '[health_check] probing %s\n' "${url}" >&2
    if ffprobe -v error -rtsp_transport tcp -timeout $((TIMEOUT * 1000000)) \
               -i "${url}" -show_entries stream=codec_name -of default=nw=1:nk=1 \
               >/dev/null 2>&1; then
        printf '[health_check]   %s OK\n' "${profile}" >&2
    else
        printf '[health_check]   %s FAILED\n' "${profile}" >&2
        failed=1
    fi
done
exit "${failed}"
```

- [ ] **Step 5: Update `scripts/start_rtsp.sh` if it contains port literals**

```bash
grep -n "8554" scripts/start_rtsp.sh
```

If any matches, replace each `8554` with `554`. Save and re-run grep — expect zero matches.

- [ ] **Step 6: Update `scripts/env.sh` port-related exports**

```bash
grep -n "8554" scripts/env.sh
```

If `RTSP_SERVER_PORT` (or similar) is exported as 8554, change it to 554. Re-run grep — expect zero matches.

- [ ] **Step 7: Rebuild MediaMTX setcap and verify**

```bash
./scripts/build.sh   # idempotent; setcap step will run
getcap third_party/install/bin/mediamtx
```

Expected: `third_party/install/bin/mediamtx cap_net_bind_service=ep`.

- [ ] **Step 8: Smoke-test MediaMTX binds :554 natively**

```bash
./scripts/start_rtsp.sh
sleep 2
ss -lntp | grep ":554 "
./scripts/teardown.sh
```

Expected: ss output shows mediamtx listening on `*:554`.

- [ ] **Step 9: Commit Task 4**

```bash
git add rtsp_server/mediamtx.yml scripts/build.sh scripts/setup.sh \
        scripts/health_check.sh scripts/start_rtsp.sh scripts/env.sh
git commit -m "refactor(rtsp): bind :554 with CAP_NET_BIND_SERVICE; new profile paths"
```

---

## Task 5: Pipeline modules (runner / analyzer / scorer / report / cpu_sampler)

**Files:**
- Modify: `runner.py`
- Modify: `analyzer.py`
- Modify: `scorer.py`
- Modify: `report.py`
- Modify: `_cpu_sampler.py` (docstring only)
- Modify: `tests/test_score_cpu.py` (adjust for new score values)

Depends on Tasks 1 and 2.

### 5.1 `runner.py` — CLI + URL + CPU gate

- [ ] **Step 1: Update argparse choices and URL template**

In `runner.py::_cli()`, replace the `--codec` argument and locate the URL composition. Apply these substitutions:

```python
# At the top of _cli() (or run_capture, wherever args are parsed):
from lib.profiles import PROFILES, FRONTEND_PORT

p.add_argument("--profile", required=True, choices=sorted(PROFILES.keys()))
# (delete the --codec argument)
```

Inside `run_capture`, change:

```python
url = f"http://localhost:8080/play?codec={codec}&autoplay=1"
```

to:

```python
url = f"http://localhost:{FRONTEND_PORT}/play?profile={profile}&autoplay=1"
```

- [ ] **Step 2: Update CPU sampler activation condition**

Find the block that starts with `if codec == "h265" and contestant_pgid is not None:` (around runner.py:235). Replace with:

```python
spec = PROFILES[profile]
sampler: _cpu_sampler.Sampler | None = None
if spec.cpu_sampled and contestant_pgid is not None:
    # ... keep the existing sampler init body unchanged, just gated by spec.cpu_sampled
    driver_pid: int | None = None
    try:
        driver_pid = browser._impl_obj._connection._transport._proc.pid
    except AttributeError:
        pass
    sampler = _cpu_sampler.Sampler(
        pgid=contestant_pgid,
        hz=cpu_sample_hz if cpu_sample_hz else _cpu_sampler.DEFAULT_SAMPLE_HZ,
        extra_root_pid=driver_pid,
    )
    sampler.start()
```

Similarly, change the `if codec == "h265" and contestant_pgid is not None:` block at the end of the capture loop (around runner.py:291) to `if spec.cpu_sampled and contestant_pgid is not None:`.

- [ ] **Step 3: Update `_write_capture_meta` signature**

In `_write_capture_meta`, change the field name written into `capture_meta.json` from `"codec"` to `"profile"`:

```python
meta = {
    "profile": profile,
    "capture_started_at_epoch": result.capture_started_at_epoch,
    "capture_ended_at_epoch": result.capture_ended_at_epoch,
    "cpu": result.cpu_sample_result,
}
```

Update the call site to pass `profile` instead of `codec`.

- [ ] **Step 4: Replace all remaining occurrences of `codec` parameter name**

Rename the `codec: str` parameter to `profile: str` throughout `run_capture` and `_cli`. The `--codec` flag is gone; the function signature should be `run_capture(profile, output, duration_s, fps, contestant_pgid=None, cpu_sample_hz=None)`.

In `_cli()`, the call becomes:

```python
result = run_capture(
    args.profile, args.output, args.duration, args.fps,
    contestant_pgid=args.contestant_pgid,
    cpu_sample_hz=args.cpu_sample_hz,
)
```

- [ ] **Step 5: Smoke-test runner.py URL composition (no browser launch needed)**

```bash
.venv/bin/python -c "
from runner import run_capture
import inspect
sig = inspect.signature(run_capture)
print('params:', list(sig.parameters.keys()))"
```

Expected: `params: ['profile', 'output', 'duration_s', 'fps', 'contestant_pgid', 'cpu_sample_hz']`.

```bash
.venv/bin/python runner.py --help 2>&1 | grep -E "profile|codec"
```

Expected: shows `--profile {2k,4k}` only; no `--codec`.

### 5.2 `analyzer.py` — CLI + metrics file name

- [ ] **Step 6: Replace `--codec` with `--profile` in analyzer**

In `analyzer.py::_cli()`:

```python
from lib.profiles import PROFILES

p.add_argument("--profile", required=True, choices=sorted(PROFILES.keys()))
# (delete --codec)
```

Replace any usage of `args.codec` with `args.profile`. Update `analyze(codec, ...)` signature to `analyze(profile, ...)`.

- [ ] **Step 7: Verify output filename is profile-driven**

In `_cli`, the existing code already writes to `args.output`. The caller (evaluator.sh) controls the filename — Task 6 will update that. No filename hardcoding needed inside analyzer.py.

- [ ] **Step 8: Smoke-test analyzer CLI**

```bash
.venv/bin/python analyzer.py --help 2>&1 | grep -E "profile|codec"
```

Expected: shows `--profile`, no `--codec`.

### 5.3 `scorer.py` — major refactor

- [ ] **Step 9: Update `tests/test_score_cpu.py` to match new scoring (TDD: rewrite test first)**

Replace the existing `tests/test_score_cpu.py` with:

```python
"""Table-driven tests for scorer.score_cpu and the cpu block in build_score."""

from __future__ import annotations

import pytest

import scorer


EXPECTED_4K = scorer.EXPECTED_FPS["4k"]


def fps_at(ratio: float) -> float:
    """Return a measured 4K fps that yields measured/expected == ratio."""
    return EXPECTED_4K * ratio


@pytest.mark.parametrize(
    "mean_cpu, expected_points",
    [
        (0.0, 10),
        (4.99, 10),
        (5.0, 10),
        (5.5, 10),
        (6.0, 10),
        (7.0, 9),
        (8.0, 9),
        (9.0, 8),
        (10.0, 7),
        (11.0, 6),
        (12.0, 6),
        (13.0, 5),
        (14.0, 4),
        (15.0, 4),
        (16.0, 3),
        (17.0, 2),
        (18.0, 1),
        (19.0, 1),
        (20.0, 0),
        (20.001, 0),
        (99.0, 0),
    ],
)
def test_score_cpu_table(mean_cpu, expected_points):
    points, reason = scorer.score_cpu(
        mean_cpu_percent=mean_cpu,
        measured_4k_fps=fps_at(0.9),
    )
    assert points == expected_points
    assert reason is None


def test_score_cpu_gates_when_fps_below_threshold():
    points, reason = scorer.score_cpu(
        mean_cpu_percent=2.0,
        measured_4k_fps=fps_at(0.24),
        gate_fps_ratio=0.25,
    )
    assert points == 0
    assert reason == "4k_fps_below_threshold"


def test_score_cpu_gates_when_sampler_missing():
    points, reason = scorer.score_cpu(
        mean_cpu_percent=None,
        measured_4k_fps=fps_at(0.9),
    )
    assert points == 0
    assert reason == "sampler_no_data"


def test_score_cpu_gate_takes_precedence_over_value():
    points, reason = scorer.score_cpu(
        mean_cpu_percent=0.0,
        measured_4k_fps=fps_at(0.10),
        gate_fps_ratio=0.25,
    )
    assert points == 0
    assert reason == "4k_fps_below_threshold"


# build_score wiring ---------------------------------------------------------

def _profile_full() -> dict:
    return {
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "measured_fps": 25.0,
    }


def _4k_full_with_cpu(mean_cpu: float | None) -> dict:
    block = _profile_full()
    if mean_cpu is not None:
        block["cpu"] = {
            "mean_percent": mean_cpu,
            "sample_count": 9,
            "sample_window_ms": 9000,
            "ncpu": 8,
            "clk_tck": 100,
            "normalization": "all_cores_total",
            "pgid": 12345,
            "sample_hz_used": 1.0,
        }
    return block


def test_build_score_max_score_is_30():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(2.0)},
        chromium_version="test",
    )
    assert out["max_score"] == 30


def test_build_score_objective_total_includes_cpu():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(2.0)},
        chromium_version="test",
    )
    # 5 correctness + 5 fps per profile = 10; plus 10 CPU = 30
    assert out["objective_total"] == 10 + 10 + 10
    assert out["2k"]["total"] == 10
    assert out["4k"]["total"] == 10
    assert out["cpu"]["points"] == 10
    assert out["cpu"]["gated"] is False
    assert out["cpu"]["gate_reason"] is None
    assert out["cpu"]["measured_on_profile"] == "4k"


def test_build_score_records_thresholds_used():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(2.0)},
        chromium_version="test",
    )
    t = out["cpu"]["thresholds_used"]
    assert set(t.keys()) == {
        "gate_fps_ratio",
        "full_percent",
        "partial_start_percent",
        "zero_percent",
        "min_samples",
        "sample_hz",
    }


def test_build_score_4k_round_failed_gates_cpu():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": None},
        chromium_version="test",
    )
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "4k_round_failed"
    assert out["cpu"]["mean_percent"] is None


def test_build_score_sampler_no_data_gates_cpu():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(None)},
        chromium_version="test",
    )
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "sampler_no_data"


def test_build_score_container_mode_unsupported():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(None)},
        chromium_version="test",
        cpu_override_reason="container_mode_unsupported",
    )
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "container_mode_unsupported"


def test_build_score_no_h26x_keys():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(2.0)},
        chromium_version="test",
    )
    assert "h264" not in out
    assert "h265" not in out
```

- [ ] **Step 10: Run the updated test suite — it should fail (scorer not yet rewritten)**

```bash
.venv/bin/python -m pytest tests/test_score_cpu.py -v
```

Expected: many failures (`AttributeError` on `scorer.EXPECTED_FPS["4k"]`, signature mismatches, etc.).

- [ ] **Step 11: Rewrite `scorer.py`**

Apply these changes to `scorer.py`:

a) Replace the `EXPECTED_FPS` literal:

```python
from lib.profiles import PROFILES

EXPECTED_FPS = {name: float(spec.fps) for name, spec in PROFILES.items()}
```

b) Rename `CPU_GATE_H265_FPS_RATIO` → `CPU_GATE_FPS_RATIO` throughout the module.

c) Replace `score_correctness`:

```python
def score_correctness(rate_wm: float, rate_color: float, mean_ssim: float) -> int:
    if rate_wm >= 0.95 and rate_color >= 0.95 and mean_ssim >= 0.90:
        return 5
    if rate_wm >= 0.80 and mean_ssim >= 0.75:
        return 2
    return 0
```

d) `score_fps` stays unchanged (already 5/3/0 with the same 0.50 / 0.25 thresholds).

e) Replace `score_cpu` signature and body:

```python
def score_cpu(
    mean_cpu_percent: float | None,
    measured_4k_fps: float,
    expected_4k_fps: float = EXPECTED_FPS["4k"],
    gate_fps_ratio: float = CPU_GATE_FPS_RATIO,
) -> tuple[int, str | None]:
    if expected_4k_fps <= 0:
        return 0, "4k_fps_below_threshold"
    if measured_4k_fps / expected_4k_fps < gate_fps_ratio:
        return 0, "4k_fps_below_threshold"
    if mean_cpu_percent is None:
        return 0, "sampler_no_data"
    if mean_cpu_percent <= CPU_FULL_THRESHOLD_PERCENT:
        return 10, None
    if mean_cpu_percent > CPU_ZERO_THRESHOLD_PERCENT:
        return 0, None
    span = CPU_ZERO_THRESHOLD_PERCENT - CPU_PARTIAL_START_PERCENT
    raw = (CPU_ZERO_THRESHOLD_PERCENT - mean_cpu_percent) / span * 10.0
    return max(0, min(10, round(raw))), None
```

f) Replace `_thresholds_used` to use the renamed constant:

```python
def _thresholds_used(sample_hz: float | None) -> dict:
    return {
        "gate_fps_ratio": CPU_GATE_FPS_RATIO,
        "full_percent": CPU_FULL_THRESHOLD_PERCENT,
        "partial_start_percent": CPU_PARTIAL_START_PERCENT,
        "zero_percent": CPU_ZERO_THRESHOLD_PERCENT,
        "min_samples": CPU_MIN_SAMPLES,
        "sample_hz": sample_hz if sample_hz is not None else 0.0,
    }
```

g) Replace `_build_cpu_block`:

```python
def _build_cpu_block(
    profile_metrics: dict,
    cpu_override_reason: str | None,
) -> dict:
    """Assemble the score.json `cpu` sub-object.

    Precedence:
        1. cpu_override_reason (e.g. "container_mode_unsupported", "host_failure")
        2. profile_metrics["4k"] is None                  → "4k_round_failed"
        3. profile_metrics["4k"]["cpu"] missing/None      → "sampler_no_data"
        4. sample_count < CPU_MIN_SAMPLES                 → "sampler_no_data"
        5. normal scoring via score_cpu
    """
    block: dict = {
        "points": 0,
        "mean_percent": None,
        "sample_count": 0,
        "sample_window_ms": 0,
        "ncpu": None,
        "normalization": "all_cores_total",
        "measured_on_profile": "4k",
        "thresholds_used": _thresholds_used(None),
        "gated": True,
        "gate_reason": None,
    }
    if cpu_override_reason:
        block["gate_reason"] = cpu_override_reason
        return block

    fourk_metrics = profile_metrics.get("4k")
    if fourk_metrics is None:
        block["gate_reason"] = "4k_round_failed"
        return block

    cpu_in = fourk_metrics.get("cpu")
    measured_4k_fps = float(fourk_metrics.get("measured_fps") or 0.0)
    if cpu_in is None:
        points, reason = score_cpu(None, measured_4k_fps)
        block["points"] = points
        block["gate_reason"] = reason or "sampler_no_data"
        return block

    sample_count = int(cpu_in.get("sample_count") or 0)
    mean_pct = cpu_in.get("mean_percent")
    if not isinstance(mean_pct, (int, float)):
        mean_pct = None
    if sample_count < CPU_MIN_SAMPLES:
        mean_pct = None

    sample_hz = cpu_in.get("sample_hz_used")
    block["mean_percent"] = mean_pct
    block["sample_count"] = sample_count
    block["sample_window_ms"] = int(cpu_in.get("sample_window_ms") or 0)
    block["ncpu"] = cpu_in.get("ncpu")
    block["normalization"] = cpu_in.get("normalization") or "all_cores_total"
    block["thresholds_used"] = _thresholds_used(sample_hz)

    points, reason = score_cpu(mean_pct, measured_4k_fps)
    block["points"] = points
    block["gate_reason"] = reason
    block["gated"] = reason is not None
    return block
```

h) Replace `score_codec` (now `score_profile`):

```python
def score_profile(profile: str, metrics: dict) -> CodecScore:
    rate_wm = float(metrics.get("watermark_recognition_rate") or 0.0)
    rate_color = float(metrics.get("color_check_rate") or 0.0)
    mean_ssim = float(metrics.get("mean_ssim") or 0.0)
    measured_fps = float(metrics.get("measured_fps") or 0.0)
    expected_fps = EXPECTED_FPS[profile]
    return CodecScore(
        codec=profile,  # field still named "codec" internally; rename in step (j)
        correctness_points=score_correctness(rate_wm, rate_color, mean_ssim),
        fps_points=score_fps(measured_fps, expected_fps),
        measured_fps=measured_fps,
        expected_fps=expected_fps,
        watermark_recognition_rate=rate_wm,
        color_check_rate=rate_color,
        mean_ssim=mean_ssim,
    )
```

i) Rename the `CodecScore` dataclass to `ProfileScore` and rename its `codec` field to `profile`:

```python
@dataclass
class ProfileScore:
    profile: str
    correctness_points: int
    fps_points: int
    measured_fps: float
    expected_fps: float
    watermark_recognition_rate: float
    color_check_rate: float
    mean_ssim: float

    @property
    def total(self) -> int:
        return self.correctness_points + self.fps_points

    def to_dict(self) -> dict:
        return {
            "correctness_points": self.correctness_points,
            "fps_points": self.fps_points,
            "total": self.total,
            "measured_fps": self.measured_fps,
            "expected_fps": self.expected_fps,
            "watermark_recognition_rate": self.watermark_recognition_rate,
            "color_check_rate": self.color_check_rate,
            "mean_ssim": self.mean_ssim,
        }
```

Update `score_profile` to use `ProfileScore(profile=profile, ...)`.

j) Replace `build_score`:

```python
def build_score(
    profile_metrics: dict[str, dict | None],
    chromium_version: str | None,
    failure_reason: str | None = None,
    per_round_reasons: dict | None = None,
    cpu_override_reason: str | None = None,
) -> dict:
    out: dict = {
        "max_score": 30,
        "objective_total": 0,
        "cpu": None,
        "chromium_version": chromium_version,
    }
    for profile in PROFILES:
        out[profile] = None

    if failure_reason:
        out["reason"] = failure_reason

    for profile, metrics in profile_metrics.items():
        if metrics is None:
            continue
        s = score_profile(profile, metrics)
        out[profile] = s.to_dict()
        out["objective_total"] += s.total

    if per_round_reasons:
        for profile, reason in per_round_reasons.items():
            if not reason:
                continue
            block = out.get(profile) or {}
            block["reason"] = reason
            out[profile] = block

    cpu_effective_override = cpu_override_reason
    if failure_reason and not cpu_effective_override:
        cpu_effective_override = "host_failure"

    cpu_block = _build_cpu_block(profile_metrics, cpu_effective_override)
    out["cpu"] = cpu_block
    out["objective_total"] += cpu_block["points"]
    return out
```

k) Replace `_cli()` to accept repeated `--metrics PROFILE=PATH`:

```python
def _cli() -> int:
    p = argparse.ArgumentParser(description="Score analyzer metrics into score.json.")
    p.add_argument("--metrics", action="append", default=[],
                   metavar="PROFILE=PATH",
                   help="Per-profile metrics JSON (repeat once per profile).")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=False)
    p.add_argument("--install-prefix", type=Path, required=False)
    p.add_argument("--failure-reason", type=str, default=None)
    # Per-profile failure reasons: --profile-reason PROFILE=REASON
    p.add_argument("--profile-reason", action="append", default=[],
                   metavar="PROFILE=REASON")
    p.add_argument("--cpu-override-reason", type=str, default=None)
    args = p.parse_args()

    def _parse_kv(items: list[str]) -> dict:
        out = {}
        for item in items:
            if "=" not in item:
                raise SystemExit(f"expected PROFILE=VALUE, got: {item!r}")
            k, v = item.split("=", 1)
            out[k] = v
        return out

    metrics_paths = _parse_kv(args.metrics)
    profile_metrics: dict[str, dict | None] = {p: None for p in PROFILES}
    for profile, path_str in metrics_paths.items():
        path = Path(path_str)
        profile_metrics[profile] = json.loads(path.read_text()) if path.exists() else None

    per_round_reasons = _parse_kv(args.profile_reason)

    score = build_score(
        profile_metrics,
        _load_chromium_version(args.install_prefix),
        failure_reason=args.failure_reason,
        per_round_reasons=per_round_reasons,
        cpu_override_reason=args.cpu_override_reason,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(score, indent=2))

    if args.report:
        from report import render_report
        render_report(
            score=score,
            profile_metrics=profile_metrics,
            output=args.report,
            run_dir=args.output.parent,
        )

    print(json.dumps(score))
    return 0
```

- [ ] **Step 12: Run the test suite — it should pass**

```bash
.venv/bin/python -m pytest tests/test_score_cpu.py tests/test_profiles.py -v
```

Expected: all green.

- [ ] **Step 13: Update `_cpu_sampler.py` docstring only**

In `_cpu_sampler.py`, find any docstring or comment that references "H.265 round" and replace with "4K profile capture window". This is comment-only; no functional change.

### 5.4 `report.py` — labels and profile loop

- [ ] **Step 14: Update `report.py` to iterate profiles**

Replace `render_report` signature to accept `profile_metrics: dict[str, dict | None]` instead of `h264_metrics, h265_metrics`. Replace all per-codec branches with a loop over `profile_metrics.items()`. Section titles change:

- "H.264 Round" → "2K Profile"
- "H.265 Round" → "4K Profile"

(Look for `h264_screenshots/` / `h265_screenshots/` path strings and update to `<profile>_screenshots/`.)

- [ ] **Step 15: Smoke-test scorer + report end-to-end with fake metrics**

```bash
mkdir -p /tmp/scoretest
cat > /tmp/scoretest/2k.json <<'JSON'
{"watermark_recognition_rate":1.0,"color_check_rate":1.0,"mean_ssim":0.95,"measured_fps":25.0}
JSON
cat > /tmp/scoretest/4k.json <<'JSON'
{"watermark_recognition_rate":1.0,"color_check_rate":1.0,"mean_ssim":0.95,"measured_fps":25.0,
 "cpu":{"mean_percent":2.0,"sample_count":9,"sample_window_ms":9000,"ncpu":8,"clk_tck":100,
        "normalization":"all_cores_total","pgid":12345,"sample_hz_used":1.0}}
JSON

.venv/bin/python scorer.py \
    --metrics 2k=/tmp/scoretest/2k.json \
    --metrics 4k=/tmp/scoretest/4k.json \
    --output /tmp/scoretest/score.json

jq '.max_score, .objective_total, .["2k"].total, .["4k"].total, .cpu.points' /tmp/scoretest/score.json
```

Expected output:
```
30
30
10
10
10
```

- [ ] **Step 16: Commit Task 5**

```bash
git add runner.py analyzer.py scorer.py report.py _cpu_sampler.py tests/test_score_cpu.py
git commit -m "refactor(pipeline): profile-based runner/analyzer/scorer/report"
```

---

## Task 6: Orchestration scripts

**Files:**
- Modify: `scripts/evaluator.sh` (full rewrite of the capture / analyze / score section)
- Modify: `scripts/_contestant_lifecycle.sh` (RTSP port, readiness URL, fuser cleanup)
- Modify: `scripts/evaluator-host.sh` (port precheck, --cap-add, readiness URL)
- Modify: `scripts/evaluator-local.sh` (port precheck, readiness URL)
- Modify: `scripts/deploy.sh` (health-check call)
- Modify: `scripts/teardown.sh` (port literal)

Depends on Tasks 1–5.

- [ ] **Step 1: Rewrite the capture / analyze / score block in `scripts/evaluator.sh`**

Replace lines that hard-code h264/h265 (currently `evaluator.sh:49-188`) with a profile loop. The replacement block:

```bash
RUN_DIR="${ROOT_DIR}/results/${RESULTS_SUBDIR}"
[[ -d "${RUN_DIR}" ]] || { printf 'evaluator: results dir does not exist: %s\n' "${RUN_DIR}" >&2; exit 65; }

LOG_FILE="${RUN_DIR}/evaluator.log"
SCORE_FILE="${RUN_DIR}/score.json"
REPORT_FILE="${RUN_DIR}/report.html"

# Tee all log lines to the run log, but keep stdout for the final score JSON.
exec 3>&1
exec > >(tee -a "${LOG_FILE}" >&2) 2>&1

log()  { printf '[evaluator] %s\n' "$*"; }
die()  { printf 'evaluator failed: %s\n' "$*"; }

# Per-profile state populated by the capture loop.
declare -A PROFILE_REASON=()

# Read profiles in deterministic order.
mapfile -t PROFILES_TO_RUN < <(.venv/bin/python -c \
    "from lib.profiles import PROFILES; print('\n'.join(sorted(PROFILES.keys())))")

FAILURE_REASON=""

write_failure_score() {
    local reason="$1"
    log "writing failure score (${reason})"
    local args=(--output "${SCORE_FILE}"
                --report "${REPORT_FILE}"
                --install-prefix "${ROOT_DIR}/third_party/install"
                --failure-reason "${reason}")
    for profile in "${PROFILES_TO_RUN[@]}"; do
        local r="${PROFILE_REASON[${profile}]:-}"
        [[ -n "${r}" ]] && args+=(--profile-reason "${profile}=${r}")
    done
    "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scorer.py" "${args[@]}" >/dev/null 2>&1 || true
}

stop_mediamtx() {
    if [[ -f "${ROOT_DIR}/rtsp_server/mediamtx.pid" ]]; then
        local pid
        pid="$(cat "${ROOT_DIR}/rtsp_server/mediamtx.pid" 2>/dev/null || true)"
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            log "stopping mediamtx pid ${pid}"
            kill -TERM "${pid}" 2>/dev/null || true
            sleep 0.5
            kill -KILL "${pid}" 2>/dev/null || true
        fi
        rm -f "${ROOT_DIR}/rtsp_server/mediamtx.pid"
    fi
}

cleanup() {
    local rc=$?
    stop_mediamtx
    if [[ ! -f "${SCORE_FILE}" ]]; then
        write_failure_score "${FAILURE_REASON:-evaluator aborted}"
    fi
    if [[ -f "${SCORE_FILE}" ]]; then
        cat "${SCORE_FILE}" >&3 || true
    fi
    exit "${rc}"
}
trap cleanup EXIT INT TERM

log "starting run team_id=${TEAM_ID} run_dir=${RUN_DIR}"
log "chromium=$(tr '\n' ' ' < "${ROOT_DIR}/third_party/install/playwright_chromium.version" 2>/dev/null)"

# Bring MediaMTX up for this run.
log "starting RTSP server"
if ! "${SCRIPT_DIR}/start_rtsp.sh"; then
    FAILURE_REASON="rtsp infrastructure failure"
    log "${FAILURE_REASON}"
    exit 70
fi
for profile in "${PROFILES_TO_RUN[@]}"; do
    if ! "${SCRIPT_DIR}/health_check.sh" "${profile}" 15; then
        FAILURE_REASON="rtsp infrastructure failure (${profile} unreadable)"
        log "${FAILURE_REASON}"
        exit 70
    fi
done

run_capture() {
    local profile="$1" out="$2"
    log "running runner.py --profile ${profile}"
    local extra_args=()
    local cpu_sampled
    cpu_sampled="$(.venv/bin/python -c \
        "from lib.profiles import PROFILES; print('1' if PROFILES['${profile}'].cpu_sampled else '0')")"
    if [[ "${cpu_sampled}" == "1" && -f "${RUN_DIR}/contestant.pid" ]]; then
        local pgid
        pgid="$(cat "${RUN_DIR}/contestant.pid" 2>/dev/null || true)"
        if [[ -n "${pgid}" ]]; then
            extra_args+=(--contestant-pgid "${pgid}")
        fi
    fi
    if "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/runner.py" \
            --profile "${profile}" --output "${out}" --duration 30 --fps 25 \
            "${extra_args[@]}"; then
        return 0
    fi
    local reason
    reason="$(python3 -c "import json,sys;print(json.load(open('${out}/timestamps.json')).get('reason') or '')" 2>/dev/null || true)"
    log "  ${profile} runner failed: ${reason}"
    PROFILE_REASON[${profile}]="${reason}"
    return 1
}

analyze_profile() {
    local profile="$1" shots="$2" metrics="$3"
    local refdir
    refdir="$(.venv/bin/python -c "from lib.profiles import PROFILES; print(PROFILES['${profile}'].reference_dir)")"
    if ! compgen -G "${shots}/shot_*.png" >/dev/null && ! compgen -G "${shots}/shot_*.jpg" >/dev/null; then
        log "no ${profile} screenshots — skipping analyzer"
        return 1
    fi
    "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/analyzer.py" \
        --profile "${profile}" \
        --screenshots "${shots}" \
        --reference "${ROOT_DIR}/${refdir}" \
        --output "${metrics}"
}

declare -A METRICS_PATH
for profile in "${PROFILES_TO_RUN[@]}"; do
    shots_dir="${RUN_DIR}/${profile}_screenshots"
    metrics_path="${RUN_DIR}/${profile}_metrics.json"
    METRICS_PATH[${profile}]="${metrics_path}"
    run_capture "${profile}" "${shots_dir}" || true
    analyze_profile "${profile}" "${shots_dir}" "${metrics_path}" || true
done

SCORER_ARGS=(--output "${SCORE_FILE}" --report "${REPORT_FILE}"
             --install-prefix "${ROOT_DIR}/third_party/install")
for profile in "${PROFILES_TO_RUN[@]}"; do
    metrics_path="${METRICS_PATH[${profile}]}"
    [[ -f "${metrics_path}" ]] && SCORER_ARGS+=(--metrics "${profile}=${metrics_path}")
    local_reason="${PROFILE_REASON[${profile}]:-}"
    [[ -n "${local_reason}" ]] && SCORER_ARGS+=(--profile-reason "${profile}=${local_reason}")
done

"${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scorer.py" "${SCORER_ARGS[@]}" >/dev/null

log "score written to ${SCORE_FILE}"
log "report written to ${REPORT_FILE}"
```

(Note: the variable name `local_reason` cannot use the `local` keyword outside a function — rename to a plain variable, or wrap the loop in a helper. The simplest tweak: replace `local local_reason=...` with `loop_reason=...`.)

- [ ] **Step 2: Update `scripts/_contestant_lifecycle.sh`**

In `clx_start_contestant`, change:

```bash
export RTSP_SERVER_PORT=8554
```

to:

```bash
export RTSP_SERVER_PORT=554
```

In `clx_wait_frontend_ready`, change the curl URL:

```bash
curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:8080/play?codec=h264&autoplay=1"
```

to:

```bash
curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:8080/play?profile=2k&autoplay=1"
```

In `clx_cleanup_contestant`, change:

```bash
fuser -k 8080/tcp 8554/tcp 2>/dev/null || true
```

to:

```bash
fuser -k 8080/tcp 554/tcp 2>/dev/null || true
```

- [ ] **Step 3: Update `scripts/evaluator-host.sh`**

```bash
grep -n "8554" scripts/evaluator-host.sh
```

Replace every `8554` with `554`. Then locate the `docker run` invocation and add `--cap-add=NET_BIND_SERVICE` to its argument list. Example:

```bash
docker run --rm --network host \
    --cap-add=NET_BIND_SERVICE \
    --user "$(id -u):$(id -g)" \
    -v "${RUN_DIR}:/work/results/${RESULTS_SUBDIR}:rw" \
    "evaluator-portable:${IMAGE_TAG}" \
    "${TEAM_ID}" "${RESULTS_SUBDIR}"
```

If the script has the `--root` branch, ensure `--cap-add=NET_BIND_SERVICE` is in both branches.

In the section that writes a fallback `score.json` via docker scorer.py, update the args to use the new flag form (no `--h264 --h265`).

- [ ] **Step 4: Update `scripts/evaluator-local.sh`**

Same substitutions: `8554` → `554`; readiness URL `?codec=h264` → `?profile=2k`. Native path needs no `--cap-add` equivalent (Task 4 set the binary capability via setcap).

- [ ] **Step 5: Update `scripts/deploy.sh`**

```bash
grep -n "h264\|h265\|8554" scripts/deploy.sh
```

For each match: if it's a health-check call like `./scripts/health_check.sh h264`, change to `./scripts/health_check.sh 2k` (and add another line for 4k). If it's a port literal, change `8554` → `554`. If it's a streams/reference path string, update to the new layout.

- [ ] **Step 6: Update `scripts/teardown.sh`**

```bash
grep -n "8554\|8080" scripts/teardown.sh
```

Replace `8554` with `554` if present. Port `8080` stays.

- [ ] **Step 7: Validate scripts pass shellcheck where applicable**

```bash
which shellcheck && shellcheck scripts/evaluator.sh scripts/_contestant_lifecycle.sh \
    scripts/evaluator-host.sh scripts/evaluator-local.sh \
    scripts/deploy.sh scripts/teardown.sh scripts/health_check.sh \
    scripts/prepare_streams.sh scripts/start_rtsp.sh
```

Expected: clean (or only existing warnings — not new ones introduced by this task).

- [ ] **Step 8: Smoke-test the native pipeline against a hello-world contestant**

```bash
./scripts/deploy.sh
./scripts/evaluator-local.sh team_smoke test_submissions/reference.zip 2>&1 | tail -30
jq '.max_score, .objective_total, keys' results/team_smoke_*/score.json
```

Expected: `max_score: 30`, `keys: [..., "2k", "4k", "cpu", ...]`. Some rounds may fail (HEVC software decode on canonical host); that's the next phase to verify.

- [ ] **Step 9: Commit Task 6**

```bash
git add scripts/evaluator.sh scripts/_contestant_lifecycle.sh scripts/evaluator-host.sh \
        scripts/evaluator-local.sh scripts/deploy.sh scripts/teardown.sh
git commit -m "refactor(scripts): profile loop + port 554 + NET_BIND_SERVICE"
```

---

## Task 7: Container image (Dockerfile)

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1: Add `libcap2-bin` to the runtime stage apt list**

In `Dockerfile`, locate the runtime stage's first `apt-get install` line and append `libcap2-bin`:

```
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg \
        python3 \
        unzip lsof procps jq libcap2-bin \
    && ...
```

- [ ] **Step 2: Add setcap as a belt-and-suspenders step**

After the runtime-stage `COPY --from=builder /work/third_party/install /work/third_party/install` line, insert:

```
# Apply CAP_NET_BIND_SERVICE to mediamtx so the binary can bind :554.
# Note: Docker drops file capabilities when COPYing across stages on some
# storage drivers, so this is also belt-and-suspenders — runtime privilege
# is granted by `docker run --cap-add=NET_BIND_SERVICE` (see evaluator-host.sh).
RUN setcap cap_net_bind_service=+ep /work/third_party/install/bin/mediamtx \
    && getcap /work/third_party/install/bin/mediamtx
```

- [ ] **Step 3: Rebuild the image and verify capability**

```bash
sg docker -c "docker build --network host -t evaluator-portable:test ."   # adjust to your docker invocation
docker run --rm evaluator-portable:test sh -c 'getcap /work/third_party/install/bin/mediamtx'
```

Expected: `... cap_net_bind_service=ep`.

- [ ] **Step 4: Smoke-test the container can bind :554 with --cap-add**

```bash
docker run --rm --network host --cap-add=NET_BIND_SERVICE --user "$(id -u):$(id -g)" \
    --entrypoint /work/third_party/install/bin/mediamtx \
    evaluator-portable:test /work/rtsp_server/mediamtx.yml &
CONT_PID=$!
sleep 2
ss -lntp | grep ":554 "
kill ${CONT_PID}
```

Expected: ss line shows the container's mediamtx bound to `*:554`.

- [ ] **Step 5: Commit Task 7**

```bash
git add Dockerfile
git commit -m "feat(docker): setcap mediamtx + libcap2-bin in runtime stage"
```

---

## Task 8: Packaging script

**Files:**
- Modify: `scripts/package.sh`

- [ ] **Step 1: Update the prerequisite check list in `scripts/package.sh`**

Search the script for the prerequisite block; it likely contains paths like `streams/h264_watermarked.mp4`. Replace the list with the new profile-based set:

```bash
PREREQS=(
    "third_party/install/bin/ffmpeg"
    "third_party/install/bin/mediamtx"
    "third_party/install/bin/tesseract"
    "third_party/install/share/tessdata/eng.traineddata"
    ".venv/bin/python"
    "streams/h265_2560_1440.mp4"
    "streams/h265_3840_2160.mp4"
    "reference/2k/frame_00000.png"
    "reference/4k/frame_00000.png"
)
```

(Keep `~/.cache/ms-playwright/chromium-*/` and `.playwright/` checks as-is — those are not profile-specific.)

- [ ] **Step 2: Verify package.sh still produces a valid bundle**

```bash
./scripts/package.sh
ls dist/
jq . dist/manifest.json
sha256sum -c dist/SHA256SUMS
```

Expected: all dist files present, `manifest.json` parses, sha256 verification passes.

- [ ] **Step 3: Commit Task 8**

```bash
git add scripts/package.sh
git commit -m "refactor(package): switch prerequisite list to profile-named assets"
```

---

## Task 9: Test fixtures rebuild

**Files:**
- Modify: `test_submissions/src/*` (all 7 fixtures)
- Modify: `scripts/test.sh` (`EXPECTED` table)
- Regenerate: `test_submissions/*.zip` via `scripts/build_test_zips.sh`

- [ ] **Step 1: Survey existing fixture sources**

```bash
ls test_submissions/src/
for dir in test_submissions/src/*/; do
    name=$(basename "${dir}")
    echo "=== ${name} ==="
    grep -rn "codec\|8554\|h264\|h265" "${dir}" | head -10
done
```

Document for each fixture which URL parameters, RTSP URLs, and codec branches it uses. This list drives the changes in Step 2.

- [ ] **Step 2: For each fixture, replace `?codec=` and RTSP URL strings**

Pattern substitutions (apply per fixture):

| Old | New |
|-----|-----|
| `?codec=h264` | `?profile=2k` |
| `?codec=h265` | `?profile=4k` |
| `rtsp://127.0.0.1:8554/test/h264` | `rtsp://127.0.0.1:554/test/h265_2560_1440` |
| `rtsp://127.0.0.1:8554/test/h265` | `rtsp://127.0.0.1:554/test/h265_3840_2160` |
| `RTSP_SERVER_PORT=8554` | `RTSP_SERVER_PORT=554` |
| Codec branching `if codec == 'h264'` | Profile branching `if profile == '2k'` (and similar) |

For `test_submissions/src/reference/`, the reference fixture's web frontend must now route both profiles to H.265 streams. If the fixture currently uses `<video>` for both, leave the markup; just update the source URLs.

- [ ] **Step 3: Regenerate test zips**

```bash
./scripts/build_test_zips.sh
ls -la test_submissions/*.zip
```

Expected: 7 zip files modified within the last minute.

- [ ] **Step 4: Update the `EXPECTED` table in `scripts/test.sh`**

In `scripts/test.sh`, replace the `EXPECTED` associative array with profile-based assertions. New `EXPECTED`:

```bash
declare -A EXPECTED=(
    [reference]="total_ge:10"
    [static_frame]="fps_2k_eq:0,fps_4k_eq:0"
    [iframe_only]="fps_2k_eq:0,fps_4k_eq:0"
    [fake_overlay]="correctness_lt:5"
    [missing_start]="reason_global:missing start.sh"
    [never_ready]="reason_2k:startup timeout"
    [missing_testid]="reason_2k:missing data-testid"
)
```

Rationale for `total_ge:10`: the canonical Ubuntu 24.04 + Chrome host has no hardware HEVC decoder; the reference fixture's reliance on `<video>` may pass 2K via Chrome's software decode but fail 4K. Setting the gate to 10 (2K profile full marks alone) accepts this. If the verify phase shows reference can pass 4K too, tighten this back up.

Replace the assertion-evaluation logic in `scripts/test.sh` (the `case` block that handles `fps_h264_eq` / `reason_h264` / etc.) with profile-named equivalents (`fps_2k_eq`, `fps_4k_eq`, `reason_2k`, `reason_4k`).

- [ ] **Step 5: Commit Task 9**

```bash
git add test_submissions/src test_submissions/*.zip scripts/test.sh
git commit -m "test(fixtures): rebuild for 2K/4K profile assertions; max_score=30"
```

---

## Task 10: Self-test execution

**Files:** none (verification only)

- [ ] **Step 1: Cold-start build & deploy on the build host**

```bash
./scripts/teardown.sh || true
./scripts/build.sh --clean
./scripts/deploy.sh
getcap third_party/install/bin/mediamtx
ls streams/ reference/
```

Expected: setcap reports `cap_net_bind_service=ep`; `streams/` contains the two new mp4s only; `reference/` contains `2k/` and `4k/` only.

- [ ] **Step 2: Run the default test suite**

```bash
./scripts/test.sh
```

Expected: per-case PASS/FAIL printed; exit zero overall. Investigate any FAIL immediately.

- [ ] **Step 3: Validate one full `score.json` matches the new shape**

```bash
RUN=$(ls -t results/ | head -1)
jq 'keys, .max_score, ."2k".total, ."4k".total, .cpu.points, .objective_total' results/${RUN}/score.json
```

Expected: `keys` contains `"2k"`, `"4k"`, `"cpu"`, no `"h264"`/`"h265"`; `max_score == 30`.

- [ ] **Step 4: Package + portable self-test (if target host available)**

```bash
./scripts/package.sh
EVAL_TARGET_HOST=user@target ./scripts/test.sh --portable
```

Expected: all four stages PASS. If `EVAL_TARGET_HOST` is unavailable, defer this to a later session and note it in the verify.md artifact.

- [ ] **Step 5: 4K software decode stability probe**

Within `results/team_smoke_*/`, inspect the 4K metrics:

```bash
jq '."4k".measured_fps, ."4k".watermark_recognition_rate, ."4k".mean_ssim, .cpu' \
    results/team_smoke_*/score.json
```

Document: does 4K stay above 25 × 0.50 = 12.5 fps (full marks band)? Above 6.25 fps (partial band)? If below 0.25 the CPU gate trips — note whether this is acceptable for the reference fixture or whether the reference fixture needs a WebCodecs/wasm decode path (out-of-scope; documented in verify.md).

- [ ] **Step 6: Commit any test-fixture tweaks discovered during run**

```bash
git status
# only commit if there are tweaks. Often this step is a no-op.
```

---

## Task 11: Documentation sync

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md` (if present)
- Modify: `dist/README.md` (regenerated by package.sh; verify it's profile-aware)

Can run in parallel with Task 12.

- [ ] **Step 1: Rewrite the relevant CLAUDE.md sections**

Open `CLAUDE.md` and update these sections:

a) **Overview paragraph** — replace the scoring breakdown text:

> Scoring breakdown: 2K H.265 profile 10 pts (5 correctness + 5 fps), 4K H.265 profile 10 pts (5 correctness + 5 fps), plus a 10-pt contestant CPU usage sub-score sampled during the 4K capture window only. CPU is measured on the contestant's PGID process group ... The CPU sub-score requires 4K fps to clear `score_fps`'s partial-credit threshold (default `measured/expected ≥ 0.25`) — otherwise it's gated to 0. ...

b) **Structure tree** — replace the entries:

```
├── reference/{h264,h265}/      # generated reference PNGs (git-ignored)
```

with:

```
├── lib/{watermark.py,profiles.py}   # watermark renderer + ProfileSpec registry
├── reference/{2k,4k}/                # generated reference PNGs (git-ignored)
```

c) **Data Flow** — replace step descriptions referencing `runner.py --codec` with `runner.py --profile`; update the bullet about CPU sampling to say "4K profile capture only".

d) **Entry Points table** — no fundamental change, but if any cell references codec, update.

e) **Conventions** — find the `Ports 8554 (RTSP) and 8080 (contestant frontend) are the evaluator's reserved set` line and replace `8554` with `554`. Add a note: "Port 554 binding requires CAP_NET_BIND_SERVICE: host-native via `setcap` in build.sh; container via `--cap-add=NET_BIND_SERVICE` in evaluator-host.sh."

f) **Source-build rule** — keep the note about x264 if Task 12 keeps the submodule; otherwise update.

- [ ] **Step 2: Sweep for residual stale strings across the repo**

```bash
grep -rn "?codec=" --include="*.py" --include="*.sh" --include="*.md" --include="*.yml" \
    --exclude-dir=.venv --exclude-dir=.git --exclude-dir=third_party --exclude-dir=openspec/changes/archive
grep -rn ":8554" --include="*.py" --include="*.sh" --include="*.md" --include="*.yml" \
    --exclude-dir=.venv --exclude-dir=.git --exclude-dir=third_party --exclude-dir=openspec/changes/archive
grep -rn "\"h264_metrics\"\|'h264_metrics'\|h264_screenshots" --include="*.py" --include="*.sh" --include="*.md" \
    --exclude-dir=.venv --exclude-dir=.git --exclude-dir=third_party --exclude-dir=openspec/changes/archive
grep -rn "max_score.*40" --include="*.py" --include="*.sh" --include="*.md" \
    --exclude-dir=.venv --exclude-dir=.git --exclude-dir=third_party --exclude-dir=openspec/changes/archive
```

For each remaining hit, either fix it or annotate with a comment explaining why it's retained (e.g., historical archive references in `openspec/changes/archive/` are intentionally untouched).

- [ ] **Step 3: Verify `dist/README.md` (regenerated by package.sh)**

```bash
./scripts/package.sh
grep -n "8554\|codec=" dist/README.md
```

Expected: no matches. If package.sh template-generates this file, find the template (likely a here-doc inside `scripts/package.sh`) and update it.

- [ ] **Step 4: Commit Task 11**

```bash
git add CLAUDE.md README.md scripts/package.sh   # whichever changed
git commit -m "docs: sync to profile-based pipeline (2K/4K, port 554, max_score=30)"
```

---

## Task 12: Optional cleanup — drop x264 submodule

**Files:**
- Modify: `.gitmodules`
- Modify: `scripts/build.sh` (remove x264 build call)
- Modify: `third_party/x264` (removed via git submodule deinit)

Low priority — only execute if Step 1 confirms x264 is not still needed by the ffmpeg configure.

- [ ] **Step 1: Check whether ffmpeg's build invokes `--enable-libx264`**

```bash
grep -n "libx264" scripts/build.sh
```

If matches: x264 IS still being linked into ffmpeg. STOP — skip the rest of Task 12 and update tasks.md to note the dependency. If no matches AND no current generated stream uses x264 (Task 3 confirms only libx265), proceed.

- [ ] **Step 2: Remove the x264 build step from `scripts/build.sh`**

Delete the block that `cd`s into `third_party/x264`, runs `./configure`, `make`, and `make install`. Save.

- [ ] **Step 3: Remove the submodule cleanly**

```bash
git submodule deinit -f third_party/x264
git rm -f third_party/x264
rm -rf .git/modules/third_party/x264
```

Edit `.gitmodules` and remove the `[submodule "third_party/x264"]` block.

- [ ] **Step 4: Verify build still succeeds**

```bash
./scripts/build.sh --clean
which ffmpeg && ffmpeg -hide_banner -encoders 2>&1 | grep -E "libx264|libx265"
```

Expected: `libx265` listed; `libx264` absent. ffmpeg still builds, prepare_streams (libx265 only) still works.

- [ ] **Step 5: Commit Task 12 (skip if Step 1 indicated keep)**

```bash
git add .gitmodules scripts/build.sh third_party/x264
git commit -m "chore(submodules): drop x264 (no longer used after H.264 removal)"
```

---

## Task 13: Final verification

**Files:** none (read-only validation)

- [ ] **Step 1: Run openspec validate**

```bash
openspec validate switch-to-h265-resolution-profiles
```

Expected: `Change 'switch-to-h265-resolution-profiles' is valid`.

- [ ] **Step 2: Full pytest pass**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all green.

- [ ] **Step 3: End-to-end test pass**

```bash
./scripts/test.sh
```

Expected: every fixture PASS.

- [ ] **Step 4: Audit a real `score.json` against the spec**

```bash
RUN=$(ls -t results/ | head -1)
jq . results/${RUN}/score.json
```

Manual checklist:
- `"max_score": 30` ✓
- `"2k"` and `"4k"` blocks present with `correctness_points`, `fps_points`, `total` ≤ 10 ✓
- `"cpu"` block with `points`, `mean_percent`, `sample_count`, `thresholds_used` (six keys), `measured_on_profile: "4k"` ✓
- No `"h264"` / `"h265"` keys ✓
- `objective_total = 2k.total + 4k.total + cpu.points` ✓

- [ ] **Step 5: Audit `report.html` rendering**

```bash
xdg-open results/${RUN}/report.html  # or copy to a host that has a browser
```

Manual checklist:
- Top summary shows 2K / 4K subtotals and CPU ✓
- Frame-number charts labeled "2K profile" / "4K profile" ✓
- Suspicious-frame gallery links to `2k_screenshots/` / `4k_screenshots/` ✓

- [ ] **Step 6: Confirm no residual codec strings in shipped artifacts**

```bash
zgrep -l "codec=" dist/evaluator-portable_*.tar.zst 2>/dev/null || true
grep -l "h264\|h265\|8554" dist/README.md dist/manifest.json 2>/dev/null || true
```

Expected: no output (`zgrep -l` and `grep -l` both silent).

- [ ] **Step 7: Final commit**

```bash
git status
# If anything is still uncommitted from earlier tasks, commit it now with a
# specific message; otherwise this is a no-op.
```

---

## Self-Review Notes

**Spec coverage check (manually walked through):**

- `evaluator/spec.md` Workspace Layout → covered by Task 1 (lib/profiles.py) and Task 3 (reference/2k|4k).
- Reference Stream Generation → Task 3.
- Local RTSP Server (including setcap, cap-add) → Task 4 + Task 7.
- Contestant Runtime Contract (env vars, URL) → Task 6 (`_contestant_lifecycle.sh` env + URL).
- Playwright Capture Runner (--profile) → Task 5.1.
- Frame Analysis (--profile + metrics naming) → Task 5.2.
- Scoring (max_score=30, 5/2/0 + 5/3/0) → Task 5.3 (with test in tests/test_score_cpu.py).
- Contestant CPU Usage Measurement (4K trigger, gate_reason) → Task 5.1 + Task 5.3 + Task 5.4.
- Report Generation → Task 5.4.
- Orchestration and Cleanup → Task 6.1.
- Result Artifacts → Task 6.1 (file naming through evaluator.sh paths).
- Anti-Cheating Behaviors (incl. 2K-only or 4K-only cheat scenario) → Task 9 fixtures cover.
- Lifecycle Scripts (setcap + libcap2-bin + port 554) → Task 4 + Task 6.
- Host Toolchain Documentation → Task 11.

- `portable-bundle/spec.md` Image Build Pipeline (new assets) → Task 8.
- Target Host Operator Entry (port 554 + --cap-add) → Task 6.3.
- Container Network Model (port 554) → Task 7.
- Failure Score → Task 6.3 (and scorer's failure path Task 5.3).
- Build Host Local Shortcut → Task 6.4.
- Target Host Prerequisites → Task 11 (docs).
- Portable Self-Test Mode → Task 10.

All requirements covered. Plan is ready for execution.
