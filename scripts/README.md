# 脚本用途

所有命令从仓库根目录运行。日志、截图、费用和报告写入忽略的 `build/`；配置与真实凭据留在本机。

| 用途 | 入口 | 说明 |
| --- | --- | --- |
| 本地服务与面板 | `serve_local_panel.py` | `start/status/stop`；独立本机数据目录 |
| 基础验证 | `smoke_test.py`、`agent_gateway_e2e.py` | Core 与 Gateway 链路检查；先核对目标环境，避免向共享账本写测试数据 |
| 规则导出 | `export_banking_rules.py` | `--check` 核对文档和 JSON 与源码一致 |
| 仓库文档 | `check_repository_docs.py` | 检查已跟踪 Markdown 的本地链接，忽略用户未跟踪笔记 |
| 场景与意图 | `benchmark_intents.py`、`evaluate_agent_scenarios.py` | 固定数据回归；模型调用按命令参数启用 |
| 多轮评测 | `evaluate_dialogue_tasks.py`、`dialogue_eval/` | 模拟用户、独立评分、状态检查与费用记录；联网运行会产生费用 |
| 失败复现 | `replay_dialogue_failures.py`、`summarize_conversation_comparison.py` | 保留原失败、重放及对照，不改写历史成绩 |
| 提示词优化 | `optimize_dialogue_prompts.py` | 可选离线 GEPA；联网优化须设置预算，不自动发布 |
| 发布门槛 | `check_dialogue_release.py`、`package_stable_candidate.py` | 完整证据检查后才能生成稳定候选源码包 |
| 参赛材料 | `package_contest_submission.py` | 按提交页面五栏整理目录，技术三份正文禁止依赖其他报告或源码，保留随文图表供 PDF 嵌入；从已提交源码导出 source/，不转换格式，不依赖或校验 PPT／视频，可选记录 APK 来源 |
| Android 隔离回归 | `run_android_regression.py`、`scenario_backend.py`、`device_signer.py`、`VerifyDeviceProto.java` | 专用场景包、临时服务、设备签名及官方 protobuf 结果核对 |
| 演示录制 | `serve_contest_demo.py` | 隔离录制服务，与设备用例配合 |
| 可用性页面 | `evaluate_usability.py` | 配合 `tools/usability/index.html`，原始观察不公开 |
| 飞书排查与验收 | `diagnose_feishu_access.py`、`feishu_acceptance.py` | 脱敏诊断及明确测试窗口内的真实消息验收 |
| 品牌导出 | `build_anyan_brand.py` | 保留的 SVG 原稿生成交付套件，需要 logo-design 技能及渲染器 |

`dialogue_eval/` 的环境、传输、计量、评分和门槛模块被上述入口及测试共同使用。Java 结果核对器、旧版数据集和场景后端仍有调用，不按文件名或年代删除。参数以各入口的 `--help` 为准；多轮评测的详细操作见[评测说明](../docs/多轮任务评测.md)。
