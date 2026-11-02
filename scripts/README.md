# 工具与脚本

所有入口默认从仓库根目录运行。先用 `--help` 核对参数；运行产物放在忽略的 `build/`。模型调用会消耗额度，消息监听和写操作需要专用测试环境，不能对共享服务直接执行。

## 日常开发

| 入口 | 用途 |
| --- | --- |
| `serve_local_panel.py start/status/stop` | 本地 Core、Gateway 与运维台，独立配置与数据目录 |
| `export_banking_rules.py` | 从同源规则与能力生成 Markdown / JSON；`--check` 只校验 |
| `check_repository_docs.py` | 检查已跟踪 Markdown 的本地导航和图像路径 |
| `smoke_test.py`、`agent_gateway_e2e.py` | 基础链路检查，可能操作演示账本，先确认目标 |

## 对话与发布证据

| 入口 | 用途 |
| --- | --- |
| `benchmark_intents.py`、`evaluate_agent_scenarios.py` | 固定意图与场景回归，模型按参数启用 |
| `evaluate_dialogue_tasks.py`、`dialogue_eval/` | 多轮模拟用户、环境硬检查、语义评分与费用记录 |
| `replay_dialogue_failures.py`、`summarize_conversation_comparison.py` | 原失败重放和对照，不改写历史成绩 |
| `optimize_dialogue_prompts.py` | 可选 GEPA 离线候选，不自动发布，真实运行设置预算 |
| `check_dialogue_release.py` | 检查完整冻结证据和运维复核 |
| `package_stable_candidate.py` | 已提交源码通过门槛后导出稳定候选，不部署 |

评测的环境、传输、计量和评分模块被多个入口和测试共享，不应当作重复代码清理。[评测说明](../docs/多轮任务评测.md)

## 设备、渠道与素材

| 入口 | 用途与约束 |
| --- | --- |
| `run_android_regression.py`、`scenario_backend.py` | Android 隔离场景、临时后端与设备结果 |
| `device_signer.py`、`VerifyDeviceProto.java` | 设备证明与官方 protobuf 结果核对，有实际调用 |
| `serve_contest_demo.py` | 保留旧命名的隔离录制服务，不代替日常后台 |
| `evaluate_usability.py` | 配合 `tools/usability/index.html`，原始观察私下保留 |
| `diagnose_feishu_access.py` | 飞书脱敏诊断，不把原始凭据写报告 |
| `feishu_acceptance.py` | 真实消息验收监听，同 App 不能与其他监听并行 |
| `build_anyan_brand.py` | 从定稿导出品牌套件，依赖本机品牌技能与渲染器 |

## 历史兼容工具

`package_contest_submission.py` 保留已有自动化与历史数据契约，属于早期材料整理入口，不是当前 GitHub 的安装方式，也不证明稳定发布通过。普通开发不需要运行它；提交材料目录不随公开文档更新而重新生成。

## 添加工具

明确输入、输出、是否联网、是否收费、是否写状态以及失败退出码。默认保持私有数据隔离，输出选择新目录，不覆盖旧证据；临时脚本、密钥、数据库和构建产物不入库。更多约定见[贡献指南](../CONTRIBUTING.md)。
