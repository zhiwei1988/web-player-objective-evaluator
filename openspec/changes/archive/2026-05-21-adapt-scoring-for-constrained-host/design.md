## Context

The evaluator currently assumes a host that can sustain 25fps capture and treats 4K as both a correctness/FPS workload and the CPU-efficiency workload. Diagnostics from the fixed Ubuntu 22.04 KVM evaluation server show that the host cannot sustain the current 25fps screenshot workload and that 4K playback often collapses to very low frame rates, making 4K CPU efficiency and threshold-based 4K FPS scoring weak signals.

The scoring method must adapt without changing the target server. The evaluator should remain auditable: every threshold, target FPS, CPU sample profile, and formula used for a run must be recorded in artifacts.

## Goals / Non-Goals

**Goals:**

- Generate 2K and 4K test streams at 20fps.
- Move CPU sampling and CPU scoring from the 4K capture round to the 2K capture round.
- Preserve 4K correctness as a required decode-quality signal.
- Replace 4K threshold-band FPS scoring with a linear absolute FPS formula out of 5 points.
- Keep the overall objective max score at 30 points unless a later change explicitly rebalances the contest.
- Make score artifacts and reports clear enough to explain the constrained-host scoring policy.

**Non-Goals:**

- Changing the fixed target server, browser version, VM configuration, or system package policy.
- Replacing screenshot-based frame analysis in this change.
- Changing contestant runtime routes, RTSP URLs, or required `[data-testid="player-video"]` contract.
- Removing 4K evaluation entirely.

## Decisions

1. **Use 20fps as the profile source and expected FPS**

   `lib.profiles.PROFILES["2k"].fps` and `PROFILES["4k"].fps` will become `20`. Stream generation, runner capture cadence, analyzer diagnostics, and scoring expected FPS will derive from `PROFILES` so the value is defined once. Generated reference frame counts become `20fps * 30s = 600` frames per profile.

   Alternative considered: keep 25fps streams and only lower capture FPS. That would reduce screenshot pressure but leave scoring tied to a source cadence the fixed host cannot observe reliably.

2. **CPU sampling moves to 2K**

   The `ProfileSpec.cpu_sampled` flag will move from `4k=True` to `2k=True`. `scripts/evaluator.sh` already derives CPU sampling from this flag, so implementation should avoid hard-coding profile names except where score semantics need to record the CPU profile.

   Alternative considered: keep CPU on 4K but gate it out when 4K FPS is too low. That makes the CPU category mostly inert on this target server.

3. **CPU scoring remains gated by playback throughput**

   CPU efficiency only has meaning when the sampled profile does enough work. The CPU gate will inspect the sampled 2K profile's measured FPS against expected 20fps. The gate reason should change from `4k_fps_below_threshold` to a profile-specific value such as `2k_fps_below_threshold`, and `score.json.cpu.measured_on_profile` should record `"2k"`.

   Alternative considered: always score CPU on 2K regardless of FPS. That would allow a stalled or static renderer with low CPU usage to earn CPU points.

4. **4K FPS becomes linear absolute-value scoring**

   4K FPS points will be computed as:

   ```text
   min(max(measured_fps, 0) / 20, 1) * 5
   ```

   rounded to two decimal places in `score.json`. A 4fps 4K run therefore earns `4 / 20 * 5 = 1.0` FPS point. This keeps 4K throughput visible but avoids brittle pass/fail bands on a constrained host.

   Alternative considered: make 4K correctness-only and remove 4K FPS points. The user explicitly requested absolute-value FPS points, so the design keeps 5 FPS points available.

5. **2K FPS keeps threshold-band scoring under the new expected FPS**

   The 2K workload remains the primary performance workload. Its current full/partial threshold-band scoring can continue, but the denominator becomes 20fps because `expected_fps` derives from the profile.

   Alternative considered: make 2K FPS linear too. The requested change only calls out linear absolute scoring for 4K.

## Risks / Trade-offs

- **Historical scores are not directly comparable** → Record target FPS and thresholds/formulas in `score.json` and document that pre-change results used 25fps and 4K CPU sampling.
- **Fractional 4K FPS points may touch assumptions that points are integers** → Update tests and report formatting to accept numeric floats for FPS points and totals.
- **CPU gate semantics can become confusing after moving profiles** → Include `measured_on_profile`, `gate_profile`, `expected_fps`, and `gate_fps_ratio` in the CPU audit block.
- **Reference fixtures must be regenerated** → Require `scripts/deploy.sh` or `scripts/prepare_streams.sh` after implementation; tests should not rely on stale 25fps reference directories.
