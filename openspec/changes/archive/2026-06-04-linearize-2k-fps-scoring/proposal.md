## Why

2K 的 FPS 评分目前用阈值档位(达到目标帧率的 85% 即满分 5、50% 给 3、否则 0),而 4K 用线性绝对评分。两套并存导致评分语义不一致、悬崖式跳变(0.49→0 而 0.50→3),且阈值机制只有 2K 一个使用者,维护成本高。统一为线性后,得分随帧率连续变化、更可解释,也消除了仅服务于单档的阈值基础设施。

## What Changes

- **2K FPS 改为线性绝对评分**:得分 = `round(min(max(measured,0)/expected, 1.0) * 5, 2)`,满分保持 5 分,值保留 2 位小数。与 4K 同一公式,只是满分为 5 而非 10。
- **BREAKING(评分口径)**:同一帧率下 2K 得分变化 —— 顶部收紧(只有跑满 20fps 才得满分,旧规则 85% 即满分),中低段放松(如 ratio=0.49 从 0 变 2.45)。`score.json` 是选手可见产物,数值与字段形状均变化。
- **拆除阈值机制**:删除 `FPS_FULL_RATIO_BY_PROFILE`、`FPS_PARTIAL_RATIO_BY_PROFILE`、`_validate_fps_thresholds()` 及 `ProfileScore` 的 `fps_full_threshold_used` / `fps_partial_threshold_used` 字段。
- **引入单一真值源**:新增 `FPS_LINEAR_FULL_SCORE_BY_PROFILE = {"2k": 5, "4k": 10}`,`score_fps` 统一成一条线性公式从该表取每档满分。
- **2K block 输出对齐 4K**:`fps_scoring_mode = "linear_absolute"`、`fps_linear_full_score = 5`,不再输出阈值字段。
- **选手可见 info 中 2K/4K FPS 项强制两位小数**:`result_info.py` 的 FPS 评分项由 `:g` 改为定点两位(`_fmt_fixed2`),与 CPU 项一致。
- **`score_fps` 未知 profile 仍报错**:判定基准从"阈值字典缺失"改为"不在 `PROFILES` 中"(行为等价,语义更准)。

## Capabilities

### New Capabilities
<!-- 无新增能力 -->

### Modified Capabilities
- `evaluator`: 修改 Scoring 需求中的 **2K FPS** 子条款(档位制 → 线性绝对,满分 5),更新 **unknown-profile** 场景(基于 `PROFILES` 判定),更新 `result.info` 格式需求(2K/4K FPS 两位小数),以及 report.html 中"4K 线性公式"描述扩展为"2K 与 4K 线性公式"。

## Impact

- **代码**:`scorer.py`(`score_fps`、常量、`_validate_fps_thresholds` 删除、`ProfileScore` 字段与 `to_dict`)、`result_info.py`(FPS 项格式化)、`report.py`(已通用处理 `linear_absolute` 且默认满分 5,基本零改动)。
- **规格**:`openspec/specs/evaluator/spec.md`(Scoring 的 2K FPS 段、评分示例、unknown-profile 场景、report 描述、`result.info` 格式描述)。
- **测试**:`tests/test_score_fps.py`(2K 档位边界用例全部重写为线性,删除阈值字典断言)、`tests/test_result_info.py`、`tests/test_report_capture_diagnostics.py`(fixture 与两位小数断言)。
- **选手影响**:`score.json` / `result.info` 的 2K FPS 数值口径与显示格式变化;`max_score` 仍为 30,各档满分不变。
