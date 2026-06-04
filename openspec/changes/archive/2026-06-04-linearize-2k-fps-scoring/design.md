## Context

`scorer.py` 当前对两个 profile 用两套 FPS 评分:

- **2K**(档位制):`score_fps` 从 `FPS_FULL_RATIO_BY_PROFILE["2k"]=0.85` 和 `FPS_PARTIAL_RATIO_BY_PROFILE["2k"]=0.50` 取阈值,返回 5 / 3 / 0。`_validate_fps_thresholds()` 在 import 时校验这两个字典,`ProfileScore` 携带 `fps_full_threshold_used` / `fps_partial_threshold_used` 并写入 `score.json`。
- **4K**(线性绝对):`round(min(max(measured,0)/expected,1.0) * 10, 2)`,block 输出 `fps_scoring_mode="linear_absolute"`、`fps_linear_full_score=10`。

阈值机制只有 2K 一个使用者。`report.py` 已通用处理 `linear_absolute` 分支且默认 `fps_linear_full_score=5`(`report.py:319/348`),`tests/test_report_capture_diagnostics.py` 也已存在 `fps_linear_full_score: 5` 的 fixture —— 报告侧早已为 5 分线性档预留。`result_info.py` 中 FPS 评分项用 `_fmt_num`(`:g`,丢尾零),仅 CPU 项用 `_fmt_fixed2`(两位小数)。

## Goals / Non-Goals

**Goals:**

- 2K FPS 改为与 4K 同构的线性绝对评分,满分仍为 5,值保留 2 位小数。
- 用单一真值源 `FPS_LINEAR_FULL_SCORE_BY_PROFILE = {"2k": 5, "4k": 10}` 取代散落的满分常量(threshold 返回值 5/3、`* 10.0` 字面量)。
- 彻底移除只服务 2K 的阈值基础设施,消除死代码。
- 选手可见 info 中 2K/4K FPS 项与 CPU 项格式一致(两位小数)。

**Non-Goals:**

- 不改 correctness 评分、decode-path gate、level-0 gate、CPU 子评分。
- 不改各 profile 满分(2K=10、4K=15、CPU=5、总分 30)。
- 不改 `expected_fps` 来源(仍取自 `PROFILES`)。
- 不引入新的 profile。

## Decisions

### 决策 1:统一为单条线性公式 + 每档满分真值表

`score_fps` 收敛为:

```python
FPS_LINEAR_FULL_SCORE_BY_PROFILE: dict[str, float] = {"2k": 5, "4k": 10}

def score_fps(measured: float, expected: float, profile: str) -> float:
    if profile not in PROFILES:
        raise KeyError(profile)
    if expected <= 0:
        return 0.0
    full = FPS_LINEAR_FULL_SCORE_BY_PROFILE[profile]
    return round(min(max(measured, 0.0) / expected, 1.0) * full, 2)
```

**为什么**:2K 与 4K 现在只有满分不同,公式完全相同;一张表 + 一条公式比"4K 特判 + 2K 阈值"更易读、更易扩展(加 profile 只需加一行表项)。符合 CLAUDE.md「profile registry 是单一真值源」的约束精神。

**备选**:保留两套分支只把 2K 阈值返回值改成线性 —— 否决,留下两条等价代码路径与无用阈值字典。

### 决策 2:彻底拆除阈值机制

删除 `FPS_FULL_RATIO_BY_PROFILE`、`FPS_PARTIAL_RATIO_BY_PROFILE`、`_validate_fps_thresholds()` 及其 import 期调用;删除 `ProfileScore` 的 `fps_full_threshold_used` / `fps_partial_threshold_used` 字段及 `to_dict` 中写它们的分支。`score_profile` 构造 `ProfileScore` 时不再传这两个字段。

`to_dict` 中原 `if self.profile in FPS_FULL_RATIO_BY_PROFILE` 与 `if self.profile == "4k"` 两个分支,合并为对所有 profile 统一输出:

```python
out["fps_scoring_mode"] = "linear_absolute"
out["fps_linear_full_score"] = FPS_LINEAR_FULL_SCORE_BY_PROFILE[self.profile]
```

不再输出 `fps_full_threshold_used` / `fps_partial_threshold_used`(连同 `=None` 也移除,字段直接消失)。

**为什么**:阈值字段对线性评分无意义,留 `None` 只会让 `score.json` 形状含糊。`report.py` 的 `fps_band` 分支(`report.py:325-331`)在两个 profile 都进入 `linear_absolute` 后自然走不到,无需改报告代码。

### 决策 3:unknown-profile 报错基准改为 `PROFILES`

原 spec 场景以"profile 不在阈值字典中"为报错条件;阈值字典移除后,改为"profile 不在 `PROFILES` 中则 `raise KeyError`"。`score_fps` 开头的 `if profile not in PROFILES: raise KeyError(profile)` 已实现该行为,语义更准(真值源是 `PROFILES`)。

### 决策 4:选手可见 FPS 项强制两位小数

`result_info.py::_build_info_lines` 中,profile 评分项当前用 `_fmt_num`。改为:FPS 项(`fps_points`)与 CPU 项一致用 `_fmt_fixed2`,correctness 项仍用 `_fmt_num`(整数,无小数需求)。

实现上给 `_SCORE_ITEMS` 增加"是否定点两位"的标记,或在渲染时按 `points_key == "fps_points" or profile_key is None` 选择格式化函数。`Runtime Metrics:` 里的 `measured_fps` 不在本次范围(那是诊断量,非评分项)。

**为什么**:线性评分产生连续值,`5.00`/`4.25`/`0.50` 统一两位比 `5`/`4.25`/`0.5` 更整齐,且与 spec 既有的"CPU 两位"风格一致。

## Risks / Trade-offs

- **[评分口径变化,选手可见]** 同一帧率下 2K 得分改变:顶部收紧(旧规则 ratio≥0.85 即满分 5,新规则需 ratio=1.0 才满分),中低段放松(ratio=0.49 从 0 变 2.45)。→ 这是改线性的设计意图,已在 proposal 标注为 BREAKING;`score.json` 与 `result.info` 数值随之变化,组织方应在赛规/公告中知会。
- **[`score.json` 形状变化]** 2K block 移除 `fps_full_threshold_used` / `fps_partial_threshold_used`,新增 `fps_scoring_mode` / `fps_linear_full_score`。→ 下游若有消费这两个字段的脚本需同步;仓库内仅 `report.py`/测试引用,本次一并更新。
- **[四舍五入边界]** `round(...,2)` 用 banker's rounding(Python 默认),与现有 4K 一致,不引入新行为。→ 测试沿用 4K 既有断言风格(如 13.4/20×10=6.7)。
- **[测试回归面]** `test_score_fps.py` 的 2K 档位边界用例(5/3/0)语义已不存在。→ 按 TDD 先重写为线性断言再改实现,避免遗留对已删常量的引用。

## Migration Plan

遵循 TDD,先红后绿:

1. 改 `tests/test_score_fps.py`:删除 2K 档位边界与阈值字典断言,新增 2K 线性断言(满分、按比例、封顶、未知 profile 报错);更新 `to_dict` 断言为 2K 也输出 `linear_absolute`/`fps_linear_full_score=5`。
2. 改 `tests/test_result_info.py`、`tests/test_report_capture_diagnostics.py`:FPS 项两位小数与 fixture 对齐。
3. 跑测试确认变红。
4. 改 `scorer.py`(常量、`score_fps`、删 `_validate_fps_thresholds`、`ProfileScore` 字段与 `to_dict`、`score_profile` 构造)。
5. 改 `result_info.py`(FPS 项格式化)。
6. 跑测试确认变绿;`report.py` 预期无需改动,若 fixture 暴露问题再补。
7. 更新 `openspec/specs/evaluator/spec.md` 对应需求与场景。

回滚:本次为纯评分逻辑 + 文档改动,无数据迁移;`git revert` 即可。
