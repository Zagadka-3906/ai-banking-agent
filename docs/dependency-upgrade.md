# 依赖安全升级记录（2026-09-15）

本轮升级由 `pip-audit 2.10.1` 驱动：升级前 `pip_audit -r requirements.txt` 报告
**103 个已知漏洞 / 13 个包**；升级后重跑同一命令输出 **No known vulnerabilities found（0 个）**。
全程未修改任何业务代码，未触碰安全边界；`pytest -q` 升级前后均为 **341 passed**。

原则：逐项选择“能消除漏洞的最小可行版本”，先核实 PyPI 依赖约束与本项目实际用到的 API，
再动手升级；只有 LangChain 家族因为漏洞修复版本集中在 1.x，才做整体大版本升级。

## 一、升级矩阵

| 包 | 角色 | 升级前 | 升级后 | 选版依据 |
| --- | --- | --- | --- | --- |
| requests | 直接 | 2.32.3 | 2.33.0 | PYSEC-2026-1872 修复于 2.32.4、PYSEC-2026-2275 修复于 2.33.0，取同时覆盖两者的最低版 |
| python-dotenv | 直接 | 1.0.1 | 1.2.2 | PYSEC-2026-2270 修复于 1.2.2 |
| cryptography | 直接 | 45.0.7 | 50.0.0 | 七个漏洞的修复版本分布在 46.0.5/46.0.6/46.0.7/48.0.1/49.0.0/50.0.0，取全覆盖最低版 50.0.0；ECDSA P-256 验签 API（`security.py` 设备信任根）在 45→50 间保持稳定，`test_device_trust.py` 全过 |
| streamlit | 直接 | 1.41.1 | 1.54.0 | PYSEC-2026-212 修复于 1.53.1、PYSEC-2026-2285 修复于 1.54.0，取全覆盖最低版 |
| fastapi | 直接 | 0.115.6 | 0.141.1 | 旧版锁死 `starlette<0.42`，无法升级 starlette；0.141.1 的约束为 `starlette>=0.46.0`（无上限），放开后可修 starlette 全部漏洞 |
| starlette | 传递→显式 pin | 0.41.3 | 1.6.0 | 六组漏洞修复版本最高需 1.3.1；显式 pin 使其进入 pip-audit 审计面 |
| pillow | 传递→显式 pin | 11.3.0 | 12.3.0 | 二十余个图像解码类漏洞修复于 12.1.1~12.3.0；显式 pin 纳入审计面 |
| langgraph | 直接 | 0.2.53 | 1.0.10 | PYSEC-2026-2194 修复于 0.3.15、PYSEC-2026-83 修复于 1.0.10，取全覆盖版本（官方稳定性承诺大版本） |
| langchain-core | 直接 | 0.3.28 | 1.3.3 | 六个漏洞中两个（PYSEC-2026-2562/-2193）仅在 1.x 修复，且 `langchain-openai 1.1.14` 硬性要求 `langchain-core>=1.2.31`，故整体升入 1.x |
| langchain-openai | 直接 | 0.2.14 | 1.1.14 | PYSEC-2026-76 修复于 1.1.14；连带要求 `openai>=2.26.0` |
| langgraph-checkpoint-sqlite | 直接 | 2.0.11 | 3.1.1 | PYSEC-2026-3636 修复于 3.1.1、CVE-2025-67644 修复于 3.0.1，取全覆盖最低版；要求 `langgraph-checkpoint>=4.1.0` |
| langgraph-checkpoint | 传递 | 2.1.2 | 4.2.0 | 三个漏洞修复版本最高需 4.1.1 |
| langgraph-sdk | 传递 | 0.1.74 | 0.3.15 | 两个漏洞均修复于 0.3.15 |
| langsmith | 传递 | 0.2.11 | 0.12.5 | 四个漏洞（含 CVE-2026-59152）修复版本最高需 0.8.18，实际随 langchain-core 1.x 解析到 0.12.5 |
| openai | 传递（连带） | 1.109.1 | 2.54.0 | `langchain-openai 1.1.14` 要求 `openai<3.0.0,>=2.26.0`，属升级的硬性连带；项目仅在配置了模型密钥时经 `ChatOpenAI` 使用，离线降级路径（`MODEL_ENABLED=False`）不受影响 |

未升级（无漏洞报告，保持原 pin 以缩小变更面）：`uvicorn[standard]==0.34.0`、`pydantic==2.10.6`、`pandas==2.2.3`、`argon2-cffi==25.1.0`。

新增传递依赖（由新版本解析引入）：`langchain-protocol`、`langgraph-prebuilt`、`httpx2`/`httpcore2`、`truststore`、`uuid-utils`、`xxhash`、`zstandard`、`annotated-doc`、`typing-inspection`。

## 二、LangGraph / LangChain 升级兼容性验证

本项目实际使用的 API 全部为 1.x 保留的核心稳定接口：
`langgraph.graph.StateGraph/END`、`interrupt()`、`Command(resume=...)`、
`langgraph.checkpoint.memory.MemorySaver`、`SqliteSaver`（经 `langgraph-checkpoint-sqlite`）、
`workflow.add_conditional_edges()`、`workflow.compile(checkpointer=...)`、
`langchain_core.messages.HumanMessage/SystemMessage`、`langchain_openai.ChatOpenAI`。
`agent.py` 对这些导入已有依赖缺失时的降级护栏，本次升级**无需任何代码适配**。

升级后专项测试（对应“意图解析 / interrupt-resume / checkpoint 持久化 / 网关 SSE”四类）：

| 专项 | 命令 | 结果 |
| --- | --- | --- |
| 1. Agent 意图解析 | `pytest -q tests/test_agent_workflow.py tests/test_planner.py` | 86 passed |
| 2. interrupt/resume | `pytest -q tests/test_agent_workflow.py -k "interrupt or resume or approval or confirm"` | 3 passed |
| 3. checkpoint 持久化 | `pytest -q tests/test_approval_store.py tests/test_ledger_persistence.py tests/test_agent_gateway.py` | 36 passed |
| 4. 网关 SSE | `pytest -q tests/test_agent_gateway.py -k "stream or sse"` | 5 passed |

全量：`pytest -q` → **341 passed**（升级前后一致）。

## 三、风险判断

1. **openai 连带大版本升级（1.109.1 → 2.54.0）**：这是唯一一处“非漏洞驱动”的连带大版本。
   项目对 `ChatOpenAI` 的使用限于意图解析（`agent._model_parse`），且未配置
   `DEEPSEEK_API_KEY` 时整条模型链路被 `MODEL_ENABLED` 开关关闭、降级为确定性离线规则解析，
   因此该连带升级对离线测试与核心业务路径无影响；在线路径由意图解析等 86 个用例覆盖。
2. **starlette TestClient 弃用警告**：升级后 pytest 出现
   `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead`。
   当前 `httpx 0.28.1` 仍被支持（仅弃用警告），测试全部通过；后续若 starlette 移除 httpx TestClient 支持，
   需将 `requirements-dev.txt` 的测试栈迁移到 `httpx2`。记录为**后续注意项**，非漏洞。
3. **显式 pin 传递依赖（starlette / pillow）**：两者原本藏在传递依赖里、不受 `pip_audit -r` 直接审计。
   现以固定版本写入 `requirements.txt`，使审计面完整；升级 FastAPI/Streamlit 时需同步复核这两个 pin。

## 四、例外清单

**无。** 升级前报告的全部 13 个包 / 103 个漏洞均已通过版本升级消除，
升级后 `pip_audit -r requirements.txt` 输出 `No known vulnerabilities found`，
不存在“暂不能升级”的遗留项，故无例外条目。
（若未来出现无法升级的漏洞，按“包名 / 当前版本 / 漏洞编号 / 受影响功能 / 当前缓解措施 / 计划修复版本”六列登记于此。）

## 五、CI 集成

`.github/workflows/ci.yml` 的 test 作业在 Lint 之后新增 **Dependency audit (pip-audit)** 步骤：
先探测 `pip_audit` 可用性与 PyPI 连通性——在线环境正常执行审计（出现已知漏洞即令 CI 失败），
离线/无网络环境输出 `pip-audit skipped` 并跳过，保证无法联网的本地运行不被该步骤卡红。

## 六、验收记录（2026-09-15 实测）

| 命令 | 结果 |
| --- | --- |
| `python -m pip install -r requirements-dev.txt` | exit 0（全部 pin 可解析） |
| `python -m pip check` | No broken requirements found |
| `python -m pip_audit -r requirements.txt --progress-spinner off` | No known vulnerabilities found |
| `python -m pytest -q` | 341 passed |
| `python -m compileall -q .` | exit 0 |

审计过程只在本地 `.venv` 与 pip 缓存中留痕，未向仓库提交任何缓存、密钥或凭证。
