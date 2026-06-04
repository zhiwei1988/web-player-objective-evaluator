## 1. 测试先行(红)

- [x] 1.1 `tests/test_score_fps.py`:删除 2K 档位边界用例(`test_score_fps_2k_full_at_boundary`、`..._just_below_full_falls_to_partial`、`..._partial_at_boundary`、`..._just_below_partial_is_zero`)与阈值字典断言(`FPS_FULL_RATIO_BY_PROFILE` / `FPS_PARTIAL_RATIO_BY_PROFILE` 的集合与 partial<full 参数化用例)
- [x] 1.2 `tests/test_score_fps.py`:新增 2K 线性用例 —— `measured=10, expected=20 → 2.5`;`measured>=expected → 5`;`measured=0 → 0.0`;分数值保留 2 位(如 `measured=17, expected=20 → 4.25`)
- [x] 1.3 `tests/test_score_fps.py`:`test_score_fps_unknown_profile_raises` 断言对不在 `PROFILES` 的 profile 抛 `KeyError`(不再依赖阈值字典缺失)
- [x] 1.4 `tests/test_score_fps.py`:更新 `to_dict` 断言 —— 2K 也输出 `fps_scoring_mode="linear_absolute"`、`fps_linear_full_score=5`,且不含 `fps_full_threshold_used` / `fps_partial_threshold_used`;4K 仍为 `linear_absolute` / `10`
- [x] 1.5 `tests/test_result_info.py`:更新 fixture 与断言 —— 2K FPS、4K FPS 评分项均为两位小数(如 `2K FPS: 4.25 / 5`、`4K FPS: 3.00 / 10`);correctness 项仍为整数
- [x] 1.6 `tests/test_report_capture_diagnostics.py`:核对/更新 2K fixture 为 `fps_scoring_mode="linear_absolute"`、`fps_linear_full_score=5`,移除阈值字段
- [x] 1.7 运行 `pytest tests/test_score_fps.py tests/test_result_info.py tests/test_report_capture_diagnostics.py` 确认按预期变红

## 2. 评分核心实现(scorer.py)

- [x] 2.1 新增 `FPS_LINEAR_FULL_SCORE_BY_PROFILE: dict[str, float] = {"2k": 5, "4k": 10}` 常量(含中文 docstring 说明每档 FPS 满分,单一真值源)
- [x] 2.2 删除 `FPS_FULL_RATIO_BY_PROFILE`、`FPS_PARTIAL_RATIO_BY_PROFILE` 常量与 `_validate_fps_thresholds()` 函数及其 import 期调用
- [x] 2.3 重写 `score_fps`:`if profile not in PROFILES: raise KeyError`;`if expected <= 0: return 0.0`;统一 `return round(min(max(measured, 0.0)/expected, 1.0) * FPS_LINEAR_FULL_SCORE_BY_PROFILE[profile], 2)`,删除 4K 特判与 2K 档位分支,更新 docstring 为线性
- [x] 2.4 `ProfileScore`:删除 `fps_full_threshold_used` / `fps_partial_threshold_used` 字段
- [x] 2.5 `ProfileScore.to_dict`:合并分支,对所有 profile 输出 `fps_scoring_mode="linear_absolute"` 与 `fps_linear_full_score=FPS_LINEAR_FULL_SCORE_BY_PROFILE[self.profile]`,不再输出阈值字段
- [x] 2.6 `score_profile`(ProfileScore 构造处,约 scorer.py:371-381)删除对已移除字段的传参

## 3. 选手可见格式(result_info.py)

- [x] 3.1 `_build_info_lines`:FPS 评分项(`fps_points`)改用 `_fmt_fixed2`(两位小数),correctness 项保持 `_fmt_num`,CPU 项不变 —— 可通过给 `_SCORE_ITEMS` 增加格式标记或按 `points_key == "fps_points"` 判定实现

## 4. 验证(绿)

- [x] 4.1 运行第 1 节涉及的测试文件,确认全部变绿
- [x] 4.2 运行 `pytest tests/test_report_decode_path.py tests/test_report_gate.py tests/test_report_capture_diagnostics.py`,确认 `report.py` 无需改动即通过;若 fixture 暴露遗留阈值引用,补修
- [x] 4.3 全量回归 `pytest`(或按用户偏好仅复跑受影响用例),确认无对已删常量/字段的残留引用

## 5. 文档同步(spec)

- [x] 5.1 将 `openspec/specs/evaluator/spec.md` 的 Scoring、Report Generation、Contest Platform Result Info 三个需求按本 change 的 delta 同步(经 `/opsx:apply` 归档时由 sync 完成,或手动核对一致)
- [x] 5.2 确认 `openspec validate linearize-2k-fps-scoring` 通过,且主 spec 与代码行为一致(2K/4K 均线性、FPS 项两位小数、unknown-profile 基于 PROFILES 报错)
