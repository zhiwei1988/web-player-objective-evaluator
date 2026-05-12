## Context

The objective portion of the web plugin-free real-time media player challenge is 30 points out of 100. Organizers will receive zipped submissions, each providing a frontend that decodes an RTSP stream entirely in-browser (no native plugins) and a thin relay backend. Two codecs must be evaluated independently: H.264 at `1920x1080@30fps` and H.265 at `2560x1440@25fps`. Manual judging at scale is infeasible and subjective — two reviewers running the same submission can produce different scores — and it does not defend well against simple cheats like static-frame playback or fake watermark overlays.

The evaluator described here runs natively on Ubuntu 24.04. It owns the RTSP stream, owns the browser process, and reads ground-truth from PNGs it generated itself, so the contestant is graded against a fixed reference rather than against another judge's opinion. Stakeholders are organizers (consume score and report) and the appeals process (audits via raw artifacts). Contestants do not see the report.

Key constraints driving the design:

- The contestant's frontend runs in-browser without plugins, so the only reliable capture surface is the rendered DOM element. We screenshot `[data-testid="player-video"]`, not the network bitstream.
- Streaming apps keep network connections open indefinitely, so Playwright `networkidle` is unusable as a readiness signal. We need an explicit window-flag handshake.
- The MP4 loops every 30 seconds via `ffmpeg -re -stream_loop -1`, so a 30-second capture may straddle the boundary. We need an absolute frame identifier baked into the picture itself, not a positional index.
- Submissions are untrusted code. We control ports, processes, and cleanup ourselves — we cannot trust the contestant's `stop.sh` to release resources.

## Goals / Non-Goals

**Goals:**

- Deterministic, reproducible scoring for the 30-point objective portion: same submission → same score (within FPS measurement noise).
- Detect the cheats explicitly listed in the proposal (static frame, I-frame-only / repeated frame, fake canvas overlay, stale rendering).
- Distinguish *contestant failures* (zero score with reason) from *organizer infrastructure failures* (run aborted, not held against the contestant).
- Produce enough audit evidence per run (screenshots, metrics, score JSON, HTML report, log) to defend the score on appeal without rerunning.
- Robust cleanup — `8080` released before and after every run, even if the contestant or evaluator crashes mid-run. The whole contestant process group is also SIGKILLed, which collects any internal ports they bound.

**Non-Goals:**

- The contestant's player implementation itself.
- The subjective 70-point portion (AI-assisted development review, CPU efficiency, browser/OS compatibility matrix, manual end-to-end latency).
- A leaderboard, persistence layer, or web UI for contestants.
- Distributed scheduling across multiple evaluator machines — one machine, one submission at a time is sufficient at this scale.
- Containerization. Per the proposal, the evaluator runs natively on the host; submissions also run natively per their own `start.sh`.

## Decisions

### Open-source deps as git submodules, built from source

All C/C++/Go runtime dependencies — `ffmpeg`, `mediamtx`, `tesseract`, `leptonica`, `libdmtx`, plus `x264` and `x265` (ffmpeg's H.264/H.265 encoders) — live under `third_party/<name>/` as git submodules pinned to specific upstream commits, and are compiled by `scripts/build.sh` into `third_party/install/{bin,lib,include}`. The evaluator's runtime then extends `PATH`, `LD_LIBRARY_PATH`, and `PKG_CONFIG_PATH` to that prefix so nothing from the host's `/usr/bin` or `/usr/lib` is used at evaluation time.

Rationale: this is the only way to defend a contestant's score on appeal six months from now. Ubuntu's `apt` repositories can change ffmpeg minor versions without warning, and a different libx264 build can shift SSIM by enough to flip a borderline submission between full and partial correctness. With submodules, "we ran your submission against ffmpeg @ commit `abc1234`" is verifiable; with apt, it is not. It also makes the evaluator portable across Ubuntu point releases and gives organizers a single audit surface (`git submodule status`) for the entire toolchain.

Alternatives considered:

- **`apt install ffmpeg mediamtx ...`** — simplest, but defeats the reproducibility argument and was rejected for that reason.
- **Vendored binary tarballs in-repo** — bypasses build cost but bloats the repo and offers no path to patch upstream bugs. Submodules give us source access for free.
- **Container image with deps baked in** — orthogonal: we could still do this on top of submodules, but adding containerization is out of scope. Submodules are the minimum viable reproducibility layer.

The two documented exceptions:

- **Python packages** (`playwright`, `pillow`, `numpy`, `scikit-image`, `pylibdmtx`, `pytesseract`) are pip-installed from pinned versions in `requirements.txt`. pip+sdist is itself a source-build path for any package that doesn't ship a wheel; for those that do (numpy, scikit-image), the wheel is the upstream-blessed reproducible artifact, and rebuilding it from source on the evaluator host produces no scoring-relevant difference. Submoduling six Python repos creates significant friction for negligible benefit.
- **Chromium** uses the Playwright-bundled binary at a pinned Playwright version. A single Chromium source build is roughly 24 hours and >100 GB of disk; we trade that against pinning the Playwright revision (which transitively pins a specific Chromium build) and documenting the revision in the report header for every run.

If either exception turns out to matter for a real appeal, a follow-up change can fold them into the submodule strategy. For the first contest, the cost-benefit clearly favors the exceptions.

### Lifecycle scripts: `setup.sh`, `build.sh`, `deploy.sh`, `test.sh`, plus `evaluator.sh`

Five orthogonal entry points, each idempotent, each safe to re-run:

- **`scripts/setup.sh`** — one-time per host. `apt install` the toolchain (`build-essential`, `cmake`, `autoconf`, `automake`, `libtool`, `pkg-config`, `nasm`, `yasm`, `golang-go`, `python3-venv`, `lsof`, `unzip`). Create `.venv/` at the repo root. Run `git submodule update --init --recursive`. Nothing scoring-relevant; just makes the host capable of running `scripts/build.sh`.
- **`build.sh`** — compile each submodule and install into `third_party/install/`. Build order: leptonica → tesseract (depends on leptonica) → libdmtx → ffmpeg → mediamtx. `pip install -r requirements.txt` into the venv. `playwright install chromium` at the version Playwright pins. Re-running is a no-op when outputs are newer than sources.
- **`scripts/deploy.sh`** — make the evaluator ready to accept submissions: call `scripts/prepare_streams.sh` if `streams/*.mp4` or `reference/<codec>/` are missing or stale; start MediaMTX via `scripts/start_rtsp.sh`; health-check `rtsp://localhost:8554/test/h264` and `.../h265` with `ffprobe` via `scripts/health_check.sh`. Print a one-line "ready" or a clear failure reason. Re-running re-verifies the stream and restarts MediaMTX if it died.
- **`test.sh`** — run the validation suite from §8 of `tasks.md`: invoke `evaluator.sh` against each `test_submissions/*.zip` and assert the expected score / failure-reason. Returns non-zero if any assertion fails. This is the regression gate for the evaluator itself, not for contestants.
- **`evaluator.sh <team_id> <submission_zip>`** — the per-submission entry point, unchanged in role from the original design. Assumes `setup.sh` + `build.sh` + `deploy.sh` have succeeded; fails loudly if `third_party/install/bin/ffmpeg` or `third_party/install/bin/mediamtx` is missing.

Rationale for splitting these out: each script has a different audience and a different failure mode. `setup.sh` failures are sysadmin problems (no sudo, no network). `build.sh` failures are toolchain problems (gcc too old, missing perl module). `deploy.sh` failures are infrastructure problems (port `8554` busy). `test.sh` failures are evaluator regressions. `evaluator.sh` failures are *either* contestant problems *or* infrastructure problems, and we already distinguish those in the spec. Collapsing any two would make the failure mode harder to attribute.

Alternative considered: a single `make` target tree. Rejected because organizers expect shell scripts in a Bash-orchestrated repo, and `make`'s dependency model adds nothing over `if [[ -x third_party/install/bin/ffmpeg ]]; then ...` checks inside each script.

### Bash entry point, Python workers

`evaluator.sh` orchestrates; `runner.py`, `analyzer.py`, `scorer.py`, `report.py` do the work. Rationale: process/port management, signal handling, and `trap`-based cleanup are clean in Bash; image analysis and Playwright are clean in Python. Mixing the two in one Python script would either lose the trap discipline or require duplicating shell utilities. Alternative considered: pure Python with `subprocess` for everything — rejected because we'd reinvent `trap` semantics and need extra care to kill child trees on signal.

### Watermark = DataMatrix + visible frame number + color blocks + timecode, all four

A single signal is forgeable; four overlapping signals are not, at least not by a contestant operating at the rendering layer.

- **DataMatrix** is the primary recognizer — fast, robust to scaling and mild blur, integer payload, easy to validate.
- **Visible frame number** is the OCR fallback when DataMatrix decode fails (e.g., compression artifacts at a frame edge).
- **Four color blocks** at fixed RGB targets catch a fake overlay: if the contestant draws their own watermark on top of, say, a blurred placeholder, the bottom strip will not match `(255,0,0)/(0,255,0)/(0,0,255)/(255,255,255)` after their compositing.
- **Timecode** is documentation for human auditors; it is rendered but not scored.

Rationale: each individual signal has known failure modes (DataMatrix breaks on rescale, OCR breaks on color noise, color blocks alone are easy to fake). Combined they form a check that a cheater can only pass by actually decoding the stream.

Alternative considered: just SSIM against the reference frame. Rejected — SSIM is positional, so a one-frame offset reads as a failure even when decoding is perfect. We need the watermark to find the right reference frame *first*, then SSIM against it.

### Frame number is encoded in the picture, not derived from screenshot index

A 30-second `stream_loop -1` loop means screenshot `K` does not correspond to source frame `K`. The contestant might begin playback at frame `~100` because of startup latency. Reading the frame number out of the picture itself decouples capture timing from ground truth. Alternative considered: assume a stable startup offset — rejected because it makes the evaluator brittle to network jitter and contestant buffering.

### MediaMTX + `ffmpeg -re -stream_loop -1 -c:v copy`

MediaMTX is a single-binary RTSP server, easy to install on Ubuntu 24.04 and configurable via YAML. `ffmpeg -re` paces output at real time; `-stream_loop -1` loops forever; `-c:v copy` avoids re-encoding so the contestant sees the exact bytes we encoded (no codec drift between reference frames and on-the-wire video). Alternative considered: re-encode on the fly with `libx264 -tune zerolatency` for lower latency — rejected because re-encoded frames would diverge from the reference PNGs, breaking SSIM.

### Force RTSP over TCP

UDP loss in a noisy lab network would inject artifacts that look like contestant failures. TCP transport eliminates that variable. Latency goes up slightly; that's acceptable because end-to-end latency is judged manually in the subjective portion, not here.

### 30 Hz capture for 30 seconds, element-only screenshots

30 Hz is the H.264 source rate, so on H.264 we capture roughly 1:1 with the source; on H.265 (25 fps source) we oversample, which means duplicate watermark frame numbers are *expected* — exactly the property the unique-frame FPS metric exploits. Element-only screenshots (`page.locator(selector).screenshot()`) avoid bringing in the surrounding page chrome, which would otherwise inflate SSIM (the page background is identical between contestants and across frames). Alternative considered: `page.screenshot(full_page=True)` — rejected because it lets a contestant pass color-block checks via static page background.

### Window flag handshake, no `networkidle`

`window.__PLAYER_READY__ = true` is the only allowed signal. Streaming network sockets stay open forever, so `networkidle` either times out or fires too late. `__PLAYER_ERROR__` is read by the runner on timeout to attribute failures correctly (contestant bug vs. our environment bug).

### Capture order is H.264 then H.265, fresh context per codec

H.264 doubles as the readiness probe (we poll `/play?codec=h264`). Doing it first means by the time H.265 starts, we already know the contestant is alive. Fresh context per codec eliminates state bleed (cached decoder, MediaSource buffers, autoplay quirks). Alternative considered: same context with `page.goto` between codecs — rejected because some contestants will inevitably leak MediaSource handles between routes.

### Scoring thresholds favor false negatives over false positives

`watermark_recognition_rate >= 0.95` and `mean_ssim >= 0.90` are strict. A genuinely good submission clears them; a marginal one drops to partial credit at `>= 0.80 / >= 0.75`. This is intentional: an organizer can grant credit on appeal by inspecting the report, but cannot easily revoke credit already awarded. Alternative considered: looser primary thresholds — rejected, because then a fake overlay that hits ~88% color-block accuracy could squeak through.

### FPS via unique watermark frame numbers, not Playwright timing

Counting unique decoded source frames divided by capture duration is the property we actually want to measure. Counting screenshots-per-second only measures Playwright's loop. A static-frame cheat produces 900 screenshots but only one unique frame number — exactly the signal we want to surface. Tolerance `+/- 1.0 fps` for full marks accommodates legitimate timing jitter without giving free credit to slightly-stuttering players.

### Internal-only HTML report

Static HTML, rendered from JSON, no external assets — opens offline on the organizer's laptop. Charts as inline SVG or canvas from a small inline JS snippet, no CDN. Rationale: appeals must be reviewable on an air-gapped review machine, and we never want a contestant to be able to scrape the report URL.

## Risks / Trade-offs

- **Headless Chromium rendering may differ from headed Chromium.** → We document headless as canonical; if a submission only renders correctly in headed mode, it is non-conformant. We may keep a headed flag for manual debugging but it does not run in production.
- **Pixel-perfect SSIM after resize is noisy at the boundaries between watermark and video.** → Compute SSIM on the full resized frame; tune thresholds against the reference submission and at least one borderline negative. If borderline cases keep oscillating, document a region-of-interest crop in a follow-up change rather than encoding ROI assumptions now.
- **`pylibdmtx` link failures on some Ubuntu builds.** → Document `libdmtx0b` as a required system package; pin `pylibdmtx` minor version once a known-good combination is identified.
- **A contestant could detect Playwright and refuse to render.** → Out of scope to fully mitigate, but the runtime contract forbids it; if a submission fingerprints headless mode it is non-conformant and scores 0 with a clear note in the report.
- **MediaMTX upgrade may rename config keys.** → MediaMTX is pinned by submodule commit, so config keys cannot drift silently. Bump the submodule deliberately and review `mediamtx.yml` as part of the bump.
- **Source builds are slow.** → `build.sh` is idempotent and incremental: rebuilding `ffmpeg` from scratch is ~10 minutes on a typical lab machine, but a no-op re-run is under a second. CI / cold-cache builds can be expected to take ~30 minutes total; we document this and provide a `build.sh --clean` flag for forced rebuilds.
- **Submodule commits drift unpinned.** → `.gitmodules` records the URL; `git submodule status` records the commit. The evaluator workspace must be checked out with `git submodule update --init --recursive --checkout`; never `--remote`. Pin upstream tags or release commits, never branch tips.
- **`ffmpeg` and `tesseract` have long ./configure flag lists with security implications.** → Capture the exact `./configure` invocations in `build.sh` and treat them as part of the auditable contract. Reviewers can grep `build.sh` to confirm e.g. `--enable-libx264 --enable-libx265` is set and no network-fetching flag is enabled.
- **Python wheel choices on Ubuntu 24.04 may differ from sdist builds.** → Pin Python packages to specific versions in `requirements.txt`. If a future scoring discrepancy traces back to a pre-built numpy/scikit-image wheel, escalate to a follow-up change that forces `--no-binary=:all:` for those packages.
- **Chromium revision drifts when Playwright is upgraded.** → `build.sh` pins the Playwright Python package version; the Chromium revision is whatever that Playwright version pins. Bumping Playwright bumps Chromium, and the report header records the Chromium build string for every run so any drift is visible after the fact.
- **Contestant process leaks beyond `8080`.** → `evaluator.sh` runs the contestant via `setsid ./start.sh` and SIGKILLs the whole process group at cleanup; that collects any internal ports they bound (relay backends, ffmpeg subprocesses, etc.) without us needing to enumerate ports ahead of time. Port-level cleanup remains for `8080` (the contract port) only.
- **Disk usage per run is ~30k PNGs across many submissions.** → Document a retention policy (e.g., compress per-run directory to a tarball after `score.json` is finalized). Not implemented in this change, but the directory layout supports it cleanly.
- **Headless Chromium does not always decode H.265 in software.** → If we hit this, the H.265 round will fail uniformly across all contestants; we'd classify that as an infrastructure issue and adjust the evaluator (e.g., enable hardware decode or relax codec list). Surface this risk in the first dry run before the contest.

## Migration Plan

Not applicable — there is no prior version. The first deploy is `prepare_streams.sh` to generate reference assets, followed by a dry run against the reference contestant submission and the negative-case submissions. If any of those produce unexpected scores, tune thresholds in `scorer.py` before the contest opens. Rollback is "do not run the evaluator" — there are no services to revert.

## Open Questions

- Should the report include a per-frame thumbnail strip for the first 30 frames of each round? Helpful for fast visual triage; costs disk and render time. Default: no, but leave room in the HTML template.
- Do we need a `--keep-going` mode that scores both codecs even if one round fails outright? Default: yes for H.265 if H.264 succeeded, no for H.264 since it doubles as the readiness probe. Confirm during dry run.
- Should `report.html` link to the contestant's submission zip path? Useful for audit, but raises a small confidentiality concern if the report is ever leaked. Default: link by relative path only, never absolute.
