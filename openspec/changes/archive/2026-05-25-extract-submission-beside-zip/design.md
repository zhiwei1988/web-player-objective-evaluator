## Context

`scripts/evaluator.sh` delegates contestant staging to `scripts/_contestant_lifecycle.sh`. Today `clx_prepare_run_dir` sets `STAGE_DIR` to `submissions/<team_id>/`, and `clx_extract_submission` clears that directory before unzipping. This creates a second workspace for the contestant that is separate from the directory where the contest platform provided the zip.

The requested behavior makes the zip's containing directory the contestant workspace. This aligns extraction, `start.sh` execution, optional `stop.sh`, and platform-facing `result.info` publication around the same directory.

## Goals / Non-Goals

**Goals:**

- Extract the contestant zip into `dirname <submission_zip>`.
- Stop creating or clearing `submissions/<team_id>/` as part of evaluator staging.
- Preserve single-top-level-directory lifting.
- Grant executable permission to all files in the contestant workspace after extraction/lifting.
- Keep run artifacts under `results/<team_id>_<timestamp>/`.

**Non-Goals:**

- Do not change scoring, capture, RTSP, or result schema behavior.
- Do not introduce a new packaging format or submission manifest.
- Do not add selective executable detection; permission handling is intentionally broad.

## Decisions

1. Use `dirname <submission_zip>` as `STAGE_DIR`.

   Rationale: the caller already passes the zip path, and the contest platform expects `result.info` beside the zip. Using the same directory for staging removes the hidden repository-local `submissions/<team_id>/` workspace.

   Alternative considered: create a sibling directory named after `team_id` beside the zip. Rejected because the requested behavior is to unzip directly into the zip's directory.

2. Do not remove the stage directory before extraction.

   Rationale: `STAGE_DIR` is now an externally owned directory that also contains the zip. Clearing it would delete the submitted zip and any platform metadata. The implementation should unzip into the directory and allow `unzip`'s normal overwrite behavior for archive entries.

   Alternative considered: delete only previously extracted files. Rejected because the evaluator has no reliable manifest of which files are platform-owned versus contestant-owned.

3. Apply broad executable permissions after layout normalization.

   Rationale: the requested rule is simple: all files should be executable after extraction. Applying the permission step after single-directory lifting ensures moved files receive the final mode.

   Alternative considered: chmod only `start.sh`, `stop.sh`, shebang scripts, and ELF binaries. Rejected because the user explicitly preferred the simpler broad permission rule.

4. Keep `result.info` publication unchanged.

   Rationale: publishing to `dirname <submission_zip>/result.info` already matches the new staging directory. Only docs/spec wording should change to stop describing `submissions/<team_id>/` as the internal staging target.

## Risks / Trade-offs

- Existing files in the zip directory may be overwritten by archive entries -> The platform/operator must provide an isolated directory per submission zip.
- Broad executable permissions may mark non-executable assets as executable -> This is accepted for operational simplicity and is limited to the submission workspace.
- Reusing one upload directory for multiple teams can leave stale files -> Tests and documentation should describe the zip directory as the per-submission workspace.
