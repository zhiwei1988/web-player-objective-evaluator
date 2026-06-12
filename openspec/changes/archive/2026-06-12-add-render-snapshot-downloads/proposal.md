## Why

Contestants need a direct way to inspect whether their submitted player rendered the evaluated 2K and 4K profiles correctly. The evaluator already captures per-profile screenshots and publishes `result.info` into the platform-visible numbered submission directory, but it does not publish any rendered visual evidence or download links for contestants.

## What Changes

- Publish one representative rendered snapshot per evaluated profile (`2k-rendered.jpg`, `4k-rendered.jpg`) under a separate public artifact root, grouped by the numbered submission directory name.
- Select the representative snapshot deterministically as the fixed middle screenshot from each profile's captured `shot_*` files.
- Add contestant-facing snapshot download links to the `result.info` `info` block when the snapshots are available.
- Build public snapshot URLs from a deployment-provided base URL and the numbered submission directory name, for example `<base_url>/2079591/2k-rendered.jpg`.
- Leave the download server outside per-run evaluator lifecycle; operators run a long-lived static HTTP server rooted at the separate public artifact root, not at the submission upload root.
- Preserve the existing `Runtime Metrics` content in `result.info`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `evaluator`: Extend the contest platform result publication contract to include contestant-visible rendered snapshots and public download links.

## Impact

- `scripts/_result_info_lifecycle.sh` or adjacent publication logic will copy selected snapshot files into `${EVALUATOR_PUBLIC_ARTIFACT_ROOT}/<numbered-submission-dir>/`.
- `result_info.py` will render snapshot links from `score.json` into the `info` block.
- `score.json` will carry the published snapshot URLs or enough structured metadata for `result_info.py` to render them.
- Tests will cover fixed-middle snapshot selection, publication into the public artifact root, URL construction, and `result.info` rendering.
- Deployment must provide a stable artifact base URL and a long-lived static file service rooted at a public artifact directory that contains only contestant-visible artifacts, not submission zips or extracted source.
