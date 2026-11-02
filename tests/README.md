# 测试目录

这里保存后端回归、工具回归、共享契约和版本化任务数据。运行入口见[测试与复现](../docs/测试用例.md)，报告解释见[验证记录](../docs/验证记录.md)。

## 找到对应测试

| 位置 | 用途 |
| --- | --- |
| `test_*.py`、`support.py`、`conftest.py` | Core、Gateway、授权、持久化、回答、工具与隔离状态 |
| `fixtures/` | 后端 / Android 共享输入契约与脱敏回归输入 |
| `data/intent_benchmark.json` | 固定意图基准 |
| `data/*conversation_cases.json` 等 | 安全、自然语言与综合场景 |
| `data/dialogue_tasks*.json` | 十项开发任务与评分协议快照 |
| `data/dialogue_holdout.json`、`dialogue_generalization.json` | 八项跨资源和四项后加任务，首跑后成为已见回归 |
| `data/dialogue_repair_regression*.json` | 六类修复任务及评分版本，版本副本不算新任务 |
| `data/dialogue_repair_holdout.json` | 六项后加任务，首跑后作为回归 |
| `data/stability_acceptance_v*.json` | 八十项稳定验收快照，实现者编写，不称外部盲测 |

Android JVM 测试位于 `android/app/src/test/`，设备 UI 测试位于 `android/app/src/androidTest/`。它们不由根目录 pytest 代跑。

## 运行原则

普通 pytest 采用隔离账本和受控模型替身，不等同联网任务。真实模型运行使用独立评测工具和显式预算，不向共享演示服务写测试交易。故障注入、重置和设备自动化先确认专用环境。

修改业务时增加能够暴露错误的行为断言，优先检查状态、权限、回执和数据归属，避免只断言某段文案。测试失败应保留原输入与原因，不能通过删数据、放宽评分或换题重跑隐藏。

## 数据版本与证据

旧数据用于回归、差异与发布校验，不能仅按文件年龄删除。新评分版本保留旧结果和摘要，不能把版本副本算成新增泛化样本。训练、验证与 holdout 分开，已参与修复的数据不再是未知评估。

只使用合成或充分脱敏数据，不提交真实个人信息、模型 Key、会话、设备签名或原始私有日志。产物写入忽略的 `build/`，完整任务与费用协议见[多轮评测](../docs/多轮任务评测.md)。
