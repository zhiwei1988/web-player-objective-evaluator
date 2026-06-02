## 1. Tests First

- [x] 1.1 Add `result_info.py` renderer tests proving `Rendered Snapshots:` appears in the `info` block when `score.json` carries published snapshot URLs.
- [x] 1.2 Add renderer tests proving missing snapshot metadata omits the `Rendered Snapshots:` section without affecting existing score, breakdown, `Runtime Metrics`, or `Execution Feedback` output.
- [x] 1.3 Add publication tests proving the fixed middle `shot_*` file is selected per profile from lexically sorted screenshots and copied to `${EVALUATOR_PUBLIC_ARTIFACT_ROOT}/<numbered-dir>/<profile>-rendered.jpg`.
- [x] 1.4 Add publication tests proving public URLs are built from `EVALUATOR_PUBLIC_ARTIFACT_BASE_URL`, the URL-encoded numbered directory basename, and the published snapshot filename.
- [x] 1.5 Add publication tests proving missing profile screenshots omit only that profile's rendered snapshot while still publishing `result.info`.

## 2. Snapshot Publication

- [x] 2.1 Implement a focused snapshot publication helper that finds each profile's captured `shot_*.jpg` / `shot_*.png` files and chooses `files[len(files) // 2]`.
- [x] 2.2 Copy selected snapshots into `${EVALUATOR_PUBLIC_ARTIFACT_ROOT}/<numbered-dir>/` as `2k-rendered.jpg` and `4k-rendered.jpg`.
- [x] 2.3 Generate structured rendered snapshot metadata containing profile label, published filename, and public URL when `EVALUATOR_PUBLIC_ARTIFACT_BASE_URL` is configured.
- [x] 2.4 Ensure path segment construction uses `basename(dirname <submission_zip>)` and URL-encodes the directory name and filename.
- [x] 2.5 Log missing source screenshots and publication failures clearly without starting or managing any HTTP static server.

## 3. Score and Result Info Integration

- [x] 3.1 Persist rendered snapshot metadata into `score.json` before invoking `result_info.py`, keeping `score.json` as the authoritative source for `result.info`.
- [x] 3.2 Update `result_info.py` to render a `Rendered Snapshots:` section in the contestant-facing `info` block from the structured metadata.
- [x] 3.3 Preserve existing `Runtime Metrics` behavior and all current `result.info` field ordering semantics.
- [x] 3.4 Keep organizer-only diagnostics, local filesystem paths, and static-server operational details out of the rendered snapshot `info` lines.

## 4. Verification

- [x] 4.1 Run the focused result info and publication test suites.
- [x] 4.2 Run the broader evaluator publication tests that cover `result.info` copy behavior.
- [x] 4.3 Run `openspec status --change add-render-snapshot-downloads` and confirm the change is apply-ready.
