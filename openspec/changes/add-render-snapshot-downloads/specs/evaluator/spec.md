## ADDED Requirements

### Requirement: Contestant Render Snapshot Downloads

For every evaluator invocation that captures profile screenshots and publishes `result.info`, the evaluator SHALL publish one contestant-visible rendered snapshot per available profile into `${EVALUATOR_PUBLIC_ARTIFACT_ROOT}/<numbered-dir>/`, using filenames `<profile>-rendered.jpg` where `<profile>` is the profile key such as `2k` or `4k` and `<numbered-dir>` is the basename of `dirname <submission_zip>`. The published snapshot SHALL be selected deterministically as the fixed middle captured screenshot from that profile's lexically sorted `shot_*.jpg` and `shot_*.png` files under `<run_dir>/<profile>_screenshots/`.

When public artifact URL configuration is available, `result.info` SHALL include a contestant-visible `Rendered Snapshots:` section in the `info` block with one download URL per published profile snapshot. The evaluator SHALL build each URL from the configured public artifact base URL, the URL-encoded basename of `dirname <submission_zip>`, and the published snapshot filename. For example, with `EVALUATOR_PUBLIC_ARTIFACT_ROOT=/home/builderuser/ClientRun/PublicArtifacts`, `EVALUATOR_PUBLIC_ARTIFACT_BASE_URL=http://10.0.0.8:8090`, and `SUBMISSION_ZIP=/home/builderuser/ClientRun/RunCode/2079591/submission.zip`, the 2K snapshot URL SHALL be `http://10.0.0.8:8090/2079591/2k-rendered.jpg`.

The evaluator SHALL NOT start, stop, or supervise the HTTP download service as part of an evaluation run. The download service SHALL be a deployment responsibility, typically a long-lived static file server rooted at `${EVALUATOR_PUBLIC_ARTIFACT_ROOT}`. The static server root MUST NOT be the shared submission upload root containing contestant zips or extracted source directories.

#### Scenario: Successful run publishes rendered snapshots into public artifact root

- **WHEN** a normal scoring run captures screenshots for profiles `2k` and `4k`
- **THEN** `${EVALUATOR_PUBLIC_ARTIFACT_ROOT}/<numbered-dir>/2k-rendered.jpg` exists and is copied from the fixed middle screenshot in `<run_dir>/2k_screenshots/`
- **THEN** `${EVALUATOR_PUBLIC_ARTIFACT_ROOT}/<numbered-dir>/4k-rendered.jpg` exists and is copied from the fixed middle screenshot in `<run_dir>/4k_screenshots/`
- **THEN** `dirname <submission_zip>/2k-rendered.jpg` and `dirname <submission_zip>/4k-rendered.jpg` do not need to exist and are not used as the public download source
- **THEN** `dirname <submission_zip>/result.info` still exists with the existing publication semantics

#### Scenario: Result info includes public snapshot URLs

- **WHEN** `EVALUATOR_PUBLIC_ARTIFACT_ROOT=/home/builderuser/ClientRun/PublicArtifacts`, `EVALUATOR_PUBLIC_ARTIFACT_BASE_URL=http://10.0.0.8:8090`, and `SUBMISSION_ZIP=/home/builderuser/ClientRun/RunCode/2079591/submission.zip`
- **AND** both rendered snapshots were published
- **THEN** the `result.info` `info` block contains `Rendered Snapshots:`
- **THEN** the `info` block contains `2K: http://10.0.0.8:8090/2079591/2k-rendered.jpg`
- **THEN** the `info` block contains `4K: http://10.0.0.8:8090/2079591/4k-rendered.jpg`

#### Scenario: Missing profile screenshot omits only that snapshot link

- **WHEN** the evaluator has no captured `shot_*.jpg` or `shot_*.png` files for profile `4k`
- **THEN** it does not publish `${EVALUATOR_PUBLIC_ARTIFACT_ROOT}/<numbered-dir>/4k-rendered.jpg`
- **THEN** it does not include a 4K rendered snapshot URL in the `result.info` `info` block
- **THEN** it still publishes any available profile snapshots and still publishes `result.info`

#### Scenario: Download service is deployment-owned

- **WHEN** the evaluator publishes rendered snapshots
- **THEN** the evaluator does not start `python3 -m http.server` or any other static file server
- **THEN** the public URLs assume an operator-managed static server rooted at `${EVALUATOR_PUBLIC_ARTIFACT_ROOT}`, not at the shared submission upload root
