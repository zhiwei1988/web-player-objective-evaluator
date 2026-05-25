## 1. Regression Tests

- [x] 1.1 Add a failing test that stages a submission zip from an isolated temporary directory and asserts extracted files land beside that zip, not under `submissions/<team_id>/`.
- [x] 1.2 Add a failing test for single-top-level-directory lifting when extraction happens beside the zip.
- [x] 1.3 Add a failing test that files extracted beside the zip, including nested helper files, receive executable permission.

## 2. Contestant Lifecycle Implementation

- [x] 2.1 Update `scripts/_contestant_lifecycle.sh` so `STAGE_DIR` is derived from `dirname <submission_zip>` instead of `submissions/<team_id>/`.
- [x] 2.2 Remove any staging behavior that clears or creates `submissions/<team_id>/` for a run.
- [x] 2.3 Preserve missing-zip, unzip failure, missing-`start.sh`, single-top-level-directory lifting, `setsid ./start.sh`, optional `stop.sh`, and cleanup behavior with the new stage directory.
- [x] 2.4 Apply broad executable permission to all files under the stage directory after extraction and lifting.

## 3. Documentation And Spec Sync

- [x] 3.1 Update `openspec/specs/evaluator/spec.md` from this change's delta after implementation.
- [x] 3.2 Update `README.md` to describe the zip directory as the contestant workspace and remove `submissions/<team_id>/` as active staging.

## 4. Verification

- [x] 4.1 Run the targeted lifecycle tests and confirm they fail before implementation and pass after implementation.
- [x] 4.2 Run `openspec validate extract-submission-beside-zip --strict`.
- [x] 4.3 Run the relevant evaluator self-test path, at minimum `scripts/test.sh --only missing_start` and one successful fixture if host prerequisites are available.
