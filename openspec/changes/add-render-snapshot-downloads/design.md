## Context

The evaluator already captures profile screenshots under each run directory and publishes `result.info` to `dirname <submission_zip>`. The contest platform stores each submitted zip in a numbered directory under a shared root such as `/home/builderuser/ClientRun/RunCode/2079591/`.

The platform does not provide a download service. Serving `/home/builderuser/ClientRun/RunCode` directly would let contestants infer or browse other numbered submission directories. Operators will instead run a long-lived static HTTP server rooted at a separate public artifact directory that contains only generated contestant-visible files, for example:

```bash
mkdir -p /home/builderuser/ClientRun/PublicArtifacts
cd /home/builderuser/ClientRun/PublicArtifacts
python3 -m http.server 8090 --bind 0.0.0.0
```

The evaluator must therefore publish image files into `/home/builderuser/ClientRun/PublicArtifacts/<numbered-dir>/` and render URLs that point at those files through the configured static server. `result.info` remains published to `dirname <submission_zip>`.

## Goals / Non-Goals

**Goals:**

- Publish one 2K and one 4K rendered contestant snapshot into a separate public artifact root.
- Choose snapshots deterministically using the fixed middle captured screenshot for each profile.
- Render contestant-facing download links in the `result.info` `info` block.
- Keep the public host/port configurable through deployment environment, not hard-coded in repository code.
- Preserve existing `Runtime Metrics` behavior in `result.info`.

**Non-Goals:**

- Start, stop, supervise, or health-check the static download server from each evaluator run.
- Add authentication, cleanup retention, or directory indexing policy for the static server.
- Change scoring formulas, capture behavior, analyzer metrics, or internal `report.html`.
- Change the existing publication target for `result.info`.

## Decisions

1. **Use a separate public artifact root for downloadable images.**

   The evaluator will continue publishing `result.info` to `dirname <submission_zip>`. It will copy `2k-rendered.jpg` and `4k-rendered.jpg` into `${EVALUATOR_PUBLIC_ARTIFACT_ROOT}/<numbered-dir>/`, where `<numbered-dir>` is `basename(dirname <submission_zip>)`.

   Alternative considered: serve `RunCode` directly and publish images next to `result.info`. This exposes neighboring submission directories and potentially submitted source zips. Publishing only generated images into a separate root narrows the HTTP surface.

2. **Use deployment-provided base URL for public links.**

   The evaluator will require both `EVALUATOR_PUBLIC_ARTIFACT_ROOT=/home/builderuser/ClientRun/PublicArtifacts` and `EVALUATOR_PUBLIC_ARTIFACT_BASE_URL=http://<judge-host-ip>:8090` before rendering public snapshot links. It will derive the numbered path segment from `basename(dirname <submission_zip>)`.

   Alternative considered: auto-detect host IP and hard-code a port. This is brittle on multi-NIC hosts, NAT, reverse proxies, and port-forwarded deployments.

3. **Do not run the download service inside evaluator lifecycle.**

   A static server should be started once and kept resident by the deployment environment. Per-run evaluator processes only publish files.

   Alternative considered: start `python3 -m http.server` per run. This creates port contention under concurrent or repeated runs and makes download availability depend on the evaluator process lifetime.

4. **Select the fixed middle captured screenshot per profile.**

   For each profile, sort captured `shot_*.jpg` and `shot_*.png` files lexically and select `files[len(files) // 2]`. Copy it as a normalized `.jpg` filename. If a profile has no captured screenshots, omit that profile's link and continue publishing the rest of `result.info`.

   Alternative considered: choose the first analyzer-recognized valid frame. That may be more semantically useful, but it couples publication to analyzer internals and can make the representative frame shift based on recognition behavior rather than capture position.

5. **Store rendered snapshot links in `score.json`.**

   `result.info` remains a projection of `score.json` plus run metadata. The publication step will add structured snapshot link metadata before invoking `result_info.py`, or the scoring path will emit it directly once enough publication context is available.

   Alternative considered: make `result_info.py` scan the filesystem. That mixes rendering with artifact discovery and makes the renderer harder to test.

## Risks / Trade-offs

- Static server unavailable or wrong base URL -> contestants see broken links. Mitigation: require explicit base URL configuration and add a focused deployment/preflight check or clear warning before links are rendered.
- Numbered directory names may include characters unsafe in URLs -> links may be malformed. Mitigation: URL-encode the derived path segment and filenames.
- Missing profile screenshots -> one or both links absent. Mitigation: omit missing links while preserving score and `result.info`; debug/log output should identify the missing snapshot source.
- Public static root may expose more files than intended. Mitigation: serve only the dedicated public artifact root, never `RunCode`; publish only rendered snapshots there. Longer-term hardening can move static serving behind nginx/caddy rules.
