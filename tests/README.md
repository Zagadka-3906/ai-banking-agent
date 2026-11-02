# 测试与数据目录

测试命令和当前证据见[测试用例](../docs/测试用例.md)与[验证记录](../docs/验证记录.md)。普通 pytest 默认使用隔离数据和受控模型替身，不等同于联网模型评测。

| 目录或文件 | 用途 |
| --- | --- |
| `test_*.py`、`support.py`、`conftest.py` | 后端、授权、持久化、回答与评测工具的回归及隔离配置 |
| `fixtures/` | Android／后端共享输入契约与真实问题脱敏后的回归样例 |
| `data/intent_benchmark.json` | 意图基准 |
| `data/*conversation_cases.json`、`natural_conversation_cases.json`、`comprehensive_user_cases.json`、`contest_acceptance_cases.json` | 固定安全、自然语言、综合与参赛场景 |
| `data/dialogue_tasks*.json` | 十项开发任务及评分协议快照 |
| `data/dialogue_holdout.json`、`dialogue_generalization.json` | 八项跨资源、四项后加任务；首次运行后已转为已见回归 |
| `data/dialogue_repair_regression*.json`、`dialogue_repair_holdout.json` | 六类修复任务、评分版本及六项后加任务；版本副本不计为新任务 |
| `data/stability_acceptance_v2.json`、`stability_acceptance_v3.json` | 八十项稳定验收快照；由实现者编写，不能称外部独立盲测 |

旧版数据仍用于发布门槛、差异核对或兼容回归。更换评分版本必须保留原始失败及其数据摘要，不能删除旧版后把新结果补记为旧成绩。Android JVM 与设备测试分别位于 `android/app/src/test/` 和 `android/app/src/androidTest/`。
