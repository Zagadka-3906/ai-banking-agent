# 依赖与安全升级记录

> 当前仓库交付独立/联网两种 Android 构建与签名 IM webhook；独立 APK 不需要 Python 运行时，Python 依赖仅服务 Core、渠道网关、Agent、IM、安全与测试，Android 依赖由 Gradle 管理。

## 1. Python 运行依赖

`requirements.txt` 是 CI 与本地安装的版本基线，`pyproject.toml` 与其同步。当前运行依赖如下：

| 包 | 版本 | 用途 |
| --- | --- | --- |
| fastapi | 0.141.1 | Core、Gateway 与 IM HTTP API |
| starlette | 1.6.0 | FastAPI 运行时；显式 pin 以纳入漏洞审计 |
| uvicorn | 0.34.0 | ASGI 服务 |
| pydantic | 2.10.6 | 请求、响应与结构化意图校验 |
| requests | 2.33.0 | Core/Gateway HTTP 传输与脚本 |
| langgraph | 1.0.10 | 对话状态机、中断与恢复 |
| langgraph-checkpoint-sqlite | 3.1.1 | 渠道会话 checkpoint 持久化 |
| langchain-openai | 1.1.14 | 可选 OpenAI 兼容模型调用 |
| langchain-core | 1.3.3 | 消息与模型抽象 |
| cryptography | 50.0.0 | Android 设备 P-256 公钥验签 |
| python-dotenv | 1.2.2 | 本地环境配置 |
| argon2-cffi | 25.1.0 | 登录口令 argon2id 校验 |

开发依赖位于 `requirements-dev.txt`，包括 pytest、httpx、ruff、mypy 和 pip-audit。仓库没有额外界面层运行依赖。

## 2. 2026-09-15 安全升级

该轮由 `pip-audit 2.10.1` 驱动。升级前历史基线报告 103 个已知漏洞、涉及 13 个包；升级后当时的在线审计结果为 `No known vulnerabilities found`。主要变化：

- `requests` 升至 2.33.0，`python-dotenv` 升至 1.2.2。
- `cryptography` 升至 50.0.0，并回归 Android P-256 设备验签。
- FastAPI 升至 0.141.1，Starlette 显式 pin 到 1.6.0。
- LangGraph/LangChain 升入 1.x 安全修复线，checkpoint SQLite 升至 3.1.1。
- `langchain-openai` 的版本约束带动 OpenAI SDK 升级；项目只通过 `ChatOpenAI` 使用它，无模型密钥时走确定性规则解析。

历史审计结果只证明当时的软件清单；每次发布仍必须重新执行在线审计。

## 3. 2026-09-22 渠道收敛

本轮删除未交付渠道的源码、容器服务和 Python 包，并完成以下同步：

1. 运行依赖只保留 FastAPI Core、Android/IM 网关、编排、安全和测试所需包。
2. Docker Compose 只保留 `core` 与 `gateway`。
3. `requirements.txt` 与 `pyproject.toml` 使用同一组直接依赖版本。
4. Android 红区签名只接受已登记 P-256 设备密钥，不再保留共享客户端签名密钥。
5. CI 继续构建 Android Debug APK 和插桩测试 APK，并对 Python 清单运行 `pip-audit`。

## 4. 兼容性关注点

1. LangGraph/LangChain 的主要使用面为 `StateGraph`、`interrupt()`、`Command(resume=...)`、Memory/SQLite saver 与消息类型；升级时需回归意图解析、暂停恢复、持久化和 SSE。
2. Starlette TestClient 仍使用 `httpx`；若后续版本移除兼容层，需要同步迁移开发测试栈。
3. `cryptography` 升级必须跑设备登记、错误公钥、签名篡改、challenge 绑定、过期和重放测试。
4. Android Gradle、Compose、Retrofit、OkHttp 与 Firebase 依赖不在 Python 审计范围内，应使用 Gradle 依赖报告和平台漏洞工具单独审查。

## 5. 验收命令

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt --progress-spinner off
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check . --ignore E402
docker compose config --quiet
cd android
.\gradlew.bat --no-daemon :app:testDebugUnitTest :app:assembleDebug :app:assembleDebugAndroidTest
```

离线环境可以完成测试和构建，但不能据此声明依赖无已知漏洞。CI 只有在能访问漏洞数据源时才把 `pip-audit` 结果作为安全结论。

## 6. 例外记录

2026-09-22 对收敛后的 `requirements.txt` 重新执行在线审计，结果为 `No known vulnerabilities found`，本次发布没有已知漏洞例外。

若某个漏洞暂时不能通过升级消除，必须记录包名、当前版本、漏洞编号、可达路径、缓解措施、负责人和计划修复版本。没有重新运行在线审计时，不应填写“无例外”。
