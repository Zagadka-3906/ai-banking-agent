# 🏦 AI Banking Agent (银行 AI 智能体沙箱系统)

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/Orchestration-LangGraph-orange.svg)](https://github.com/langchain-ai/langgraph)
[![Backend](https://img.shields.io/badge/Backend-FastAPI-green.svg)](https://fastapi.tiangolo.com/)
[![Frontend](https://img.shields.io/badge/Frontend-Streamlit-red.svg)](https://streamlit.io/)
[![Android](https://img.shields.io/badge/Client-Kotlin%20%2B%20Compose-3DDC84.svg)](android/)
[![License](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

基于 **LangGraph 确定性状态机**、**受限核心银行沙箱 (Mock Core Banking)** 与 **DeepSeek 大模型** 构建的受控银行 AI 智能体系统（代号“安言”）。

本项目针对银行业务的高安全、严风控需求设计，严格践行 **“决策与执行物理分离”** 与 **“低风险自动、高风险确认、极高风险强验证”** 的权限分级理念。大模型仅负责自然语言意图理解与任务 DAG 规划，账户变更由独立的确定性 Mock Core Banking API 统一执行，彻底杜绝模型幻觉与越权写账。

系统有三个入口，面向同一套核心与同一条安全边界：

| 入口 | 进程 | 说明 |
| :--- | :--- | :--- |
| **渠道网关** | `agent_api.py` (:8100) | APP / IM 等原生客户端的 HTTP 界面：会话、LangGraph 图、持久化 checkpoint、服务端审批句柄与 SSE 流式回复。绝不接触账本，也绝不把签名 nonce 交给客户端 |
| **Android 原生客户端** | `android/` | Kotlin + Compose：登录、流式对话、黄区确认、红区 OTP + BiometricPrompt + Keystore 签名、结果页、FCM 推送 |
| **Streamlit 演示前端** | `web_app.py` (:8501) | 离线可演示的交互看板，直接驱动核心，便于答辩与本地调试 |

三者都只通过 HTTP 调用同一个 **唯一记账边界**（`main.py`，:8000）。

---

## 🌟 核心特性

- **三级权限与 Human-in-the-loop 挂起恢复机制**：
  - 🟢 **绿区（只读直通）**：账户余额、流水明细、理财产品查询与事实对比、卡片/订阅查询及风险评估，Agent 解析后直通调用核心只读接口并引用真实流水号；只读结果由 Agent 渲染为**可读清单**（产品按风险/年化/起购/流动性列示、订阅含下次扣款、卡片含当日已用与剩余额度），不向用户暴露原始 JSON。
  - 🟡 **黄区（交互确认）**：小额转账（日累计 $\le 1000$ 元）、预约/AA 分摊发起、跨场景生日资金锁定（$\le 1000$ 元）、虚拟卡申请及订阅代扣取消，状态机触发 `interrupt()` 挂起，前端弹出操作确认卡片，经用户显式确认后恢复执行。
  - 🔴 **红区（强因子鉴权）**：大额转账（日累计 $> 1000$ 元）、理财申购与赎回、卡片状态机变更（额度调整/冻结/挂失/解挂）及凭证版本轮换，强制触发服务端 OTP 短信与**挑战-签名人脸票据**核验（服务端签发绑定交易 `jti` 的活体挑战，模拟设备 SDK 签名回证，布尔自证不再生效），并下发绑定父交易 `jti` 的一次性 `mfa_proof` 凭证方可结算；Agent 侧同时校验 `required_actions` 因子是否齐备。
- **账单深度分析与事实聚合**：
  - 支持普通查账与深度诊断模式解耦，覆盖 TODAY、MONTH、LAST_MONTH、YEAR、ALL 多周期过滤；
  - 类目聚合在受限只读沙箱内执行（`facts.analysis_engine` 记录实际执行路径，沙箱不可用时确定性降级），退款（负金额）单列统计、深夜规则按业务时区墙钟判定；智能定位深夜大额、境外未知商户等可疑盗刷流水，严格校验数值与流水事实一致性，核心数据不完整时坚决不声称成功。
- **跨场景事件联动与资金隔离**：
  - 智能识别复合意图（如“下周爱人生日预留 1000 元并在前两天提醒订花和蛋糕”），生成带依赖的任务 DAG，并由 `PlanExecutor` 按依赖顺序真实推进（已执行步骤标记 SUCCEEDED，核心任务中心接管的步骤保持 PENDING）；
  - **多轮澄清与槽位回填**：澄清后的裸答会按缺失槽位的类型解释（“下周六”→日期、“3000”→金额、“李四”→收款人）并回到上一个待澄清的意图继续办理；用户已明确说出的日期/金额/收款人不会被模型输出抹掉；面向用户的确认卡与回执使用业务语言（如“把卡片 CARD-001 的日限额调整为 ¥3,000.00”），不暴露内部意图编码；
  - **模糊表达 / 省略上下文 / 隐含意图**：隐含说法映射到白名单意图（“钱都花哪儿去了”→上月深度账单分析、“我的卡好像被人盗刷了”→冻结卡片、“不想再被扣会员费了”→取消订阅、“转点钱给老婆”→转账待补金额）；回指短语按上下文解析（“再转一次”→复用上次收款人与金额并**重新走一遍授权与确认卡**、“把它取消掉”→承接上一次订阅查询）；
  - **一句话多个诉求拆解为有序 DAG**：“查一下余额，再分析上个月账单”会拆成两个只读子任务并编译成带依赖的 DAG（`0_fetch_balance → 1_fetch_transactions → 1_compute_facts`）依次执行、分别校验后合并作答；**含有变更类子请求时不会静默拆解或半做**，而是提示逐条办理；
  - 结算请求携带 `Idempotency-Key`（传输失败只幂等重试一次），结算失败时执行补偿动作（核心账本原子回滚确认，异常升级人工接管）；
  - 联动底层资金池隔离锁定专款（`balance - locked_amount` 硬扣除），任务中心自动生成提前 2 天的备忘提醒与鲜花/蛋糕订单草稿（到期保持 `WAITING_USER` 状态，不自动扣款，必须经用户二次确认）。
- **全链路金融级原生安全防御**：
  - **决策与执行物理分离**：核心结算接口与大模型物理隔离，无底层签发的安全凭证直接请求 API 一律返回 `403 Forbidden`；
  - **业务参数强绑定**：签名 Token 内嵌 `context_hash`，覆盖收款人、金额与商户等参数，防止中间人篡改；
  - **Prompt 注入拦截**：输入阶段全面消毒净化，有效阻断指令覆盖、层级逃逸与越狱攻击；
  - **账户级异常熔断**：MFA 或 Token 连续失败自动触发账户级安全锁定（HTTP 423 + Retry-After）；
  - **行为组合熔断**：敏感操作产出带权重的行为信号，按滑动窗口累计评分，命中组合规则（大额转账后挂失卡片、大额转账后改密、调额后立即大额转出、连续大额转账/多次卡片变更、多次失败后成功操作）即锁定账户并记录可解释的 `BEHAVIOR_RISK` 原因（`/api/v1/security/behavior/{user_id}` 可查评分明细）；
  - **网关入口控制**：真实登录（`POST /api/v1/auth/login`，口令用 argon2id 校验，可选短信 OTP）+ 带 `jti` 的会话令牌（`BANK_REQUIRE_SESSION=1` 时写操作强制要求，且**会话主体覆盖客户端自称身份**）+ 每主体请求窗口限流（超限 429 + Retry-After，探针豁免）；
  - **设备信任根**：App 在 Android Keystore 内生成 P-256 密钥并注册公钥，服务端按**设备公钥**验签绑定交易 `jti` 的活体挑战；共享 HMAC 票据只在 `BANK_DEMO_MODE` 下作演示捷径。凭据内的公钥不会被客户端伪造——设备编号由公钥派生（`DEV` + `sha256(SPKI)` 前 24 位），注册天然幂等且无法冒用他机；
  - **审计哈希链与决策链路**：全量关键操作生成结构化审计事件并计算 Hash-chain，支持防篡改自检验证；同一次业务请求由 `request_id` 串联为完整决策链（`/api/v1/security/audit/trace/{request_id}`：预检 → 确认 → MFA → 结算 → 凭证消费），配置 `BANK_AUDIT_LOG_PATH` 时审计文件在重启后按链校验回放续链。
- **原生客户端渠道（APP）与可达性保障**：
  - 渠道网关只分发**不透明审批句柄**（`APR…`，TTL 110s），客户端永远拿不到签名 nonce 与 MFA proof；网关自身不判定身份，而是回核心校验会话，因此会话注册表只有一份；
  - 回复支持 SSE 流式（`POST /api/v1/agent/messages/stream`）：先推阶段帧，最后**恰好一个**终态帧（`result` / `approval` / `error`）；
  - 推送投递（FCM HTTP v1）区分三种状态而不含糊：未注册设备记 `NO_DEVICE`、服务端缺少凭据记 `NOT_SENT` 并附原因、真实送达记 `SENT`；`/api/v1/security/health` 的 `push` 段把「服务端能不能发」与「设备有没有令牌」分开报告；
  - 设备注册表可落盘（`BANK_DEVICE_STORE_PATH`）：不配置时核心重启会丢失全部设备与投递令牌（推送静默失效），配置后跨重启存活；落盘文件带完整性摘要，校验失败则拒绝加载**并冻结写入**，避免后续注册覆盖掉证据。

---

## 🏗️ 系统架构设计

系统整体采用 **“感知规划层 $\to$ 安全合规层 $\to$ 确定性执行层”** 的分层流水线设计：

```text
[ 用户自然语言输入 (APP / IM / Streamlit) ]
                      │
            HTTP / REST (JSON)
                      ▼
[ 1. Intent Parser & Sanitizer ] ──► (输入净化、防注入拦截、白名单意图/槽位提取)
                      │
                      ▼
[ 2. Risk Checker & Policy Gateway ] ──► 调用核心风控预检接口 (/risk/precheck)
                      │
        ┌─────────────┼─────────────────────────────┐
        ▼             ▼                             ▼
  【绿区: 查询】 【黄区: 小额/锁定/申请】       【红区: 超额/理财/挂失】
        │             │                             │
        │     interrupt() 挂起状态机        interrupt() 挂起状态机
        │     等待用户卡片点击确认          服务端强验: 动态 OTP + 签名人脸票据
        │             │                             │
        │             └──────────────┬──────────────┘
        │                            ▼ 服务端签发短时效 Nonce / MFA Proof
        ▼                            ▼
  [ 3. Execution Gateway ] ──► 携带 x-auth-token 请求核心执行端点 (/execute)
        │                            │
        │                            ▼ (唯一记账边界: 验签、验可用余额、防重放、记录审计链)
        ▼                            ▼
  [ 4. Responder Node ] ──► 整合真实交易凭据 / 流水图表 / 待办卡片输出友好回复
```

---

## 📁 目录结构与模块职责

```text
mock-banking-core/
├── main.py              # FastAPI Mock Core：唯一记账边界、风控策略网关与安全端点
├── agent.py             # 基于 LangGraph 的业务状态机编排、HITL 中断/恢复与 DeepSeek 节点
├── agent_api.py         # 渠道网关（:8100）：APP/IM 的 HTTP 界面、会话校验、SSE 流式回复、审批句柄
├── conversation.py      # 渠道会话服务：按会话持有编译后的图、持久化 checkpoint、服务端审批注册表
├── planner.py           # 意图白名单、槽位提取、风险路由映射、跨场景任务 DAG 与事实聚合
├── security.py          # HMAC 凭证签名、登录口令（argon2id）、设备信任根、审计哈希链、熔断与注入防御
├── notifications.py     # 推送投递：设备令牌注册表、可审计派发、FCM HTTP v1（服务账号换 OAuth2 令牌）
├── scheduler.py         # 线程安全任务中心（定时转账/代扣提醒/订单草稿/人工接管队列）
├── sandbox.py           # 受限只读表达式沙箱（AST 白名单 + 进程/线程隔离 + 超时），账单聚合经此执行
├── bank_tools.py        # 独立 HTTP 传输适配层（供外部集成方与旧静态调用兼容；主链路 agent.py 自持 _http）
├── web_app.py           # Streamlit 前端交互系统（支持卡片挂起、MFA 模态弹窗与实时看板）
├── android/             # 原生 Android 客户端（Kotlin + Compose）
│   ├── app/src/main/java/com/aibanking/agent/   # 界面、会话、Keystore 设备密钥、推送
│   ├── app/src/androidTest/java/com/aibanking/agent/  # 插桩 UI 测试（绿/黄/红三条链路）
│   └── tools/                       # 工具链安装脚本、BiometricPrompt 应答脚本
├── tests/               # 自动化测试套件（17 个文件 / 300 个用例）
│   ├── conftest.py                  # 套件级强制配置（见「贡献者指南」，改动前务必阅读）
│   ├── test_core_api.py             # 核心契约、金额与风险边界、状态/卡片拒绝路径
│   ├── test_security_regressions.py # 身份、票据绑定、上下文篡改、审计、回滚
│   ├── test_concurrency.py          # 并发幂等、同一 nonce 单次记账、并发锁资
│   ├── test_gateway_controls.py     # 会话令牌、身份覆盖、限流 429 与豁免
│   ├── test_behavior_circuit.py     # 行为组合熔断：规则触发、阈值可配、锁定与恢复
│   ├── test_agent_workflow.py       # LangGraph 中断/恢复、DAG 账本、补偿、幂等重试
│   ├── test_planner.py              # 意图白名单、风险阈值、账单事实、计划执行器
│   ├── test_security_primitives.py  # 凭证签名、注入检测、审计链与持久化回放
│   ├── test_extended_scenarios.py   # 理财/卡片/订阅/AA 收款/任务接管与链路还原
│   ├── test_login.py                # 真实登录：argon2id 口令、可选短信 OTP、会话吊销
│   ├── test_device_trust.py         # 设备信任根：Keystore 公钥注册与挑战-签名验签
│   ├── test_device_store.py         # 设备注册表落盘：跨重启存活、伪造/损坏拒绝、写入冻结
│   ├── test_ledger_persistence.py   # 账本落盘：跨重启续账与篡改拒绝
│   ├── test_approval_store.py       # 审批注册表落盘：重启后待审批不丢
│   ├── test_contact_book.py         # 联系人簿：增删改与转账直接解析新联系人
│   ├── test_agent_gateway.py        # 渠道网关：审批句柄、SSE 流式、会话转发
│   └── test_push_notifications.py   # 推送：令牌绑定、可审计派发、FCM 流程、死令牌清理
├── scripts/
│   ├── smoke_test.py            # 容器冒烟验收（/healthz + 余额可读且非负）
│   ├── agent_gateway_e2e.py     # 网关三段式授权探针（严格会话模式下 64 项检查）
│   └── fcm_delivery_e2e.py      # 真实推送投递探针（要求审计 SENT 且 delivered>=1）
├── .github/workflows/
│   └── ci.yml           # CI：test 作业（compileall + pytest + ruff + compose config）+ android 作业（产出 APK）
├── docs/                # 架构设计、安全自评报告、赛题验收表、演示脚本、答辩提纲、真机验收手册
├── requirements.txt     # 项目运行时依赖清单
├── requirements-dev.txt # 开发与安全测试依赖清单
├── docker-compose.yml   # 容器化编排配置文件
├── Dockerfile           # 镜像构建文件
├── .env.example         # 环境变量配置模板文件
├── LICENSE              # MIT 许可证
└── README.md            # 项目完整技术文档与运行指南
```

---

## 🚀 快速启动指南

### 1. 克隆代码仓库

```bash
git clone https://github.com/Zagadka-3906/ai-banking-agent.git
cd ai-banking-agent
```

### 2. 创建并激活虚拟环境

本项目推荐使用 **Python 3.10–3.12**（推荐 3.12，请勿使用 Python 3.13/3.14）：

- **Windows (PowerShell)**:
  ```powershell
  python -m venv .venv
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
  .\.venv\Scripts\Activate.ps1
  ```
- **macOS / Linux**:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```

### 3. 安装依赖组件

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
# 开发与测试环境补装：
python -m pip install -r requirements-dev.txt
```

### 4. 配置环境变量

复制环境模板为本地 `.env`：

```bash
cp .env.example .env
```

编辑 `.env` 配置文件：

```dotenv
# 必须配置为长随机字符串，用于 HMAC 签名（同时作为设备库摘要的密钥）
BANK_AUTH_SECRET=your_super_secret_key_change_in_production
BANK_DEMO_MODE=1
BANK_MFA_TEST_OTP=888888
# 人脸票据密钥：模拟设备 SDK 用它对服务端挑战签名（生产必须随机且隔离）
BANK_FACE_ATTEST_SECRET=demo-face-attest-secret-change-me
BANK_REQUIRE_CLIENT_AUTH=0
# 网关控制：会话令牌（1 = 写操作强制要求 POST /auth/login 签发的令牌）与限流窗口
BANK_REQUIRE_SESSION=0
BANK_RATE_LIMIT_PER_MINUTE=240
BANK_SESSION_TTL_SECONDS=900
BANK_API_BASE=http://127.0.0.1:8000/api/v1
DEEPSEEK_API_KEY=your_deepseek_api_key_here
# 可选：审计追加文件路径；配置后重启会按哈希链校验回放续链
BANK_AUDIT_LOG_PATH=
# 可选：设备注册表落盘文件；不配置则设备公钥与推送令牌只存在内存里，重启即丢
BANK_DEVICE_STORE_PATH=
# 可选：核心账本落盘文件；配置后每笔已提交变更原子快照并在启动时重放，
# 账户/流水/锁定/风控累计跨重启保留（HMAC 完整性校验，校验失败拒载并冻结写入）
BANK_LEDGER_STORE_PATH=
# 可选：审计管理员令牌；同时决定 /tasks/run-due 是否要求管理员身份
BANK_ADMIN_AUDIT_TOKEN=
# 渠道网关（agent_api.py）
BANK_CONVERSATION_DB=agent_conversations.sqlite3
# 待审批注册表：默认随会话库落盘（<BANK_CONVERSATION_DB>.approvals.json），
# 网关重启后待审批仍可决策；设为 memory 可关闭，或指定其他文件路径
BANK_APPROVAL_STORE_PATH=
BANK_APPROVAL_TTL_SECONDS=110
BANK_AGENT_RATE_LIMIT_PER_MINUTE=60
# 推送（服务端一半）：Firebase 服务账号私钥路径，放在仓库之外
BANK_FCM_CREDENTIALS=
```

完整的 46 个变量及其含义见 [`.env.example`](.env.example)，其中每一项都带注释说明"不配置会怎样"。

> **安全须知**：共享演示或容器部署时，建议设置 `BANK_DEMO_MODE=0`、`BANK_REQUIRE_CLIENT_AUTH=1` 并注入随机的 `BANK_AUTH_SECRET` / `BANK_FACE_ATTEST_SECRET`。本地演示即使关闭严格鉴权，所有写接口与确认/MFA 接口仍必须携带 `X-User-Id`。若未配置在线模型密钥，系统将自动平滑降级为确定性离线规则解析引擎。

---

## 🖥️ 运行与交互演示

系统采用前后端解耦架构。最小演示只需终端 1；要跑原生客户端再加终端 2。

### 终端 1：启动 Mock 核心银行沙箱 (Port: 8000)

```powershell
python -m uvicorn main:app --reload --port 8000
```
> 启动后可直接访问 Swagger 可视化文档进行接口自测：`http://127.0.0.1:8000/docs`；健康检查：`http://127.0.0.1:8000/healthz`。
> 要看"当前这台机器能不能真的发推送"，直接读 `http://127.0.0.1:8000/api/v1/security/health` 的 `push` 段——它把「服务端能不能发」和「设备有没有令牌」分成两件事报告，而不是含糊地报一个成功。

### 终端 2：启动渠道网关 (Port: 8100)——Android / IM 客户端走这里

```powershell
$env:BANK_CONVERSATION_DB = 'agent_conversations.sqlite3'
python -m uvicorn agent_api:app --reload --port 8100
```
> `BANK_REQUIRE_SESSION=1` 时客户端必须先 `POST /api/v1/auth/login` 换取会话令牌，再带 `X-Session-Token` 调用 `/api/v1/agent/messages`。网关自己不做身份判定，而是回核心校验会话——会话注册表只有核心一份。

### 终端 3（可选）：启动 Streamlit 交互前端 (Port: 8501)

```powershell
python -m streamlit run web_app.py
```
> 启动后浏览器将自动打开交互界面：`http://127.0.0.1:8501`。它直接驱动核心，不经过网关，适合离线演示与答辩。

### Android 客户端

工具链安装、模拟器与设备凭据、**手动点测（含中文输入怎么做）**、插桩测试与通过标准见 **[`docs/device-verification.md`](docs/device-verification.md)**。摘要：

```powershell
pwsh -File android\tools\install-android-toolchain.ps1     # JDK 17 + Android SDK + Gradle（免管理员）
cd android; .\gradlew.bat --no-daemon assembleDebug        # 产出 app\build\outputs\apk\debug\app-debug.apk
```

没有 `android/app/google-services.json` 时应用照常构建运行，只是推送关闭（构建日志会明确打印降级原因）；放入该文件即自动启用推送，无需改构建脚本。

---

## 🧪 演示场景验证指南

在前端输入框中输入以下指令，可直接检验系统的各项风控与业务能力：

| 测试场景            | 推荐测试提示词                                               | 风控级别 | 预期系统表现                                                 |
| :------------------ | :----------------------------------------------------------- | :------- | :----------------------------------------------------------- |
| **1. 绿区秒查**     | `帮我查一下现在账户里还有多少钱`                             | 🟢 绿区   | 无需确认直接执行，返回总资产、锁定金额与可用余额三项一致的数据事实。 |
| **2. 普通查账**     | `查看今日账单`                                               | 🟢 绿区   | 仅拉取当天的交易流水与支出总计，不虚构数据，严格引用真实流水号。 |
| **3. 黄色操作确认** | `给我老婆转账 300 块买菜`                                    | 🟡 黄区   | 自动匹配联系人别名，日累计 $\le 1000$ 元；弹出操作确认卡片，点击确认后出账并生成流水号。 |
| **4. 红色大额阻断** | `给李四转 2000 元`                                           | 🔴 红区   | 当日累计超 1000 元触发风控；强制调出人脸票据与动态验证码（测试码：`888888`）弹窗，未验则拒绝。 |
| **5. 账单深度排查** | `为什么我上个月花销这么大，有异常交易吗？`                   | 🟢 绿区   | 触发深度诊断模式，只读沙箱定位深夜境外 999 元盗刷流水，生成开销分布可视化图表与建议。 |
| **6. 跨场景协同**   | `我爱人生日是下周，帮我锁定 1000 元活期，生日前 2 天提醒我订花和蛋糕` | 🟡/🔴 联动 | 确认后可用余额扣减 1000 元，侧边栏锁定资金增加，生成任务 DAG 并在任务中心挂载备忘与草稿。 |
| **7. 扩展业务办理** | `帮我对比 MMF001 和 BOND002，并取消 Apple 订阅`              | 🟢/🟡 组合 | 呈现两款理财产品的真实年化与风险对比；取消代扣弹出黄色确认卡，审批后核销并留痕。 |

---

## 🛡️ 安全合规自评要点

1. **防凭证伪造与参数绑定 (Anti-Forging Token & Context Binding)**：
   - 核心系统仅认可经过 HMAC 签名的 Nonce 凭证，且凭证内嵌覆盖金额、收款人与商户等参数的 `context_hash`。任何跳过风控预检、伪造或篡改 Header `x-auth-token` 的请求直接阻断并返回 `403 Forbidden`。
2. **状态隔离与幂等性保证 (One-Time Execution & Idempotency)**：
   - 每个安全 Token 与 MFA Proof 具备单次有效性（结算成功后标记 `used: True`），配合 `Idempotency-Key` 在并发与重试下保证**至多一笔记账**（并发证据见 `tests/test_concurrency.py`）。
3. **资金隔离完整性 (Hard Balance Isolation)**：
   - 跨场景锁定的资金计入 `locked_amount`，在计算转账限额与可用资金时严格执行 `available_balance = total_balance - locked_amount` 硬扣除，保障款项专款专用，杜绝负锁资。
4. **异常熔断与故障原子性 (Circuit Breaker & Rollback)**：
   - 连续认证失败触发账户级安全锁定（HTTP 423 + Retry-After）；内存记账配备快照回滚机制，防止任务执行中断留下半笔交易脏数据。

---

## 📊 自动化测试与质量保障

`tests/` 共 17 个文件 / 300 个用例，全部离线可跑（不需要网络、不需要大模型密钥、不写任何真实数据文件）。

```powershell
# 运行全部测试
python -m pytest -q                       # 300 passed

# 核心门禁四件套——提交前必须全过，CI（.github/workflows/ci.yml）跑的就是这些
python -m compileall -q .
python -m pytest -q
python -m ruff check . --ignore E402
docker compose config --quiet

# 按专项运行
python -m pytest -q tests/test_core_api.py              # 核心契约与金额/风险边界
python -m pytest -q tests/test_security_regressions.py  # 安全攻击与回滚回归
python -m pytest -q tests/test_concurrency.py           # 并发幂等与单次记账
python -m pytest -q tests/test_gateway_controls.py      # 会话令牌与限流
python -m pytest -q tests/test_behavior_circuit.py      # 行为组合熔断
python -m pytest -q tests/test_agent_gateway.py         # 渠道网关：审批句柄、SSE 流式
python -m pytest -q tests/test_device_trust.py          # 设备信任根与挑战签名
python -m pytest -q tests/test_push_notifications.py    # 推送派发与 FCM 流程
```

### 端到端探针（需要真实栈在跑）

```powershell
python scripts/smoke_test.py http://127.0.0.1:8000   # 容器冒烟：/healthz + 余额可读
python scripts/agent_gateway_e2e.py                  # 网关三段式授权、审批句柄、审计连续性
python scripts/fcm_delivery_e2e.py                   # 真实推送投递（需 FCM 凭据 + 已注册设备）
```

`fcm_delivery_e2e.py` 会在开头断言「服务端配好了推送凭据」与「设备持有投递令牌」**同时**成立，走完审批→调度→派发后要求审计为 `SENT` 且 `delivered >= 1`。`NOT_SENT` 判为失败而不是跳过——它的意义就是证明消息真的发出去了。

### Android 侧

```powershell
cd android
.\gradlew.bat --no-daemon assembleDebug assembleDebugAndroidTest   # CI 的 android 作业跑这个，产物上传为 bank-agent-debug-apk
.\gradlew.bat --no-daemon connectedDebugAndroidTest                # 插桩 UI 测试，需要栈与模拟器，见真机验收手册
```

---

## 🤝 贡献者指南：如何继续开发

### 先读这四份，按顺序

1. [`docs/architecture.md`](docs/architecture.md) —— 分层、接口与数据模型；
2. [`docs/security-self-assessment.md`](docs/security-self-assessment.md) —— 安全设计意图与 P0/P1/P2 整改清单（想动安全边界先看这里）；
3. [`docs/implementation-guide.md`](docs/implementation-guide.md) —— 改造顺序与"每一步先立可验证边界"的方法论；
4. [`docs/device-verification.md`](docs/device-verification.md) —— 真机/模拟器验收怎么跑、什么算通过。

### 三条不可越过的边界

改动前请先确认没有违反这三条，它们比任何功能都重要：

1. **Agent 不得直接写账。** 所有账户变更只能经 `main.py` 的「预检 → 授权 → 执行」三段式端点。`agent.py` 不导入账本对象；`bank_tools.py` 只是 HTTP 适配层。
2. **签名 nonce 与 MFA proof 不得交给客户端。** 渠道网关只分发不透明的审批句柄（`APR…`），客户端拿不到 `x-auth-token`。任何"为了方便让前端直接拿 token"的改动都是回退。
3. **不得为通过测试或方便演示而降低风险档位。** 金额阈值、红区必需因子（`USER_CLICK_CONFIRM` / `SMS_OTP` / `FACE_RECOGNITION`）、一次性消费与绑定校验都属于安全不变量。

### 常见扩展怎么做

**加一个业务意图**（改动面最大的路径，建议按此顺序）：

1. `planner.py`：加进 `Intent` 白名单并映射风险档位；需要槽位就补 `_model_parse` 的合并规则（模型不得覆盖用户明确说出的日期/金额/收款人）；
2. `main.py`：加核心预检与执行端点，执行端**再次**校验用户、账户状态、目标、金额、余额、幂等键与凭证——不能信任 Agent 传来的字段；
3. `agent.py`：意图节点、`required_actions` 校验与回复字段校验（面向用户用业务语言，不暴露内部意图编码）；
4. `security.py`：补审计事件（`action` + `decision`/`outcome` + `metadata`），保证同一次请求能被 `request_id` 串成单链；
5. `tests/`：正例 + 拒绝路径 + 边界值，参考 `tests/test_extended_scenarios.py` 的组织方式。

**加一个核心端点**：先写契约测试（`tests/test_core_api.py` 的风格），再实现；写操作必须走 `authorize_execution`，并在审计里留痕。

**Android 端加界面或接口**：`android/app/src/main/java/com/aibanking/agent/data/` 放 Retrofit 接口与仓库；界面用 Compose 并给交互控件加 `testTag`（`ui/UiTags.kt`），否则插桩测试无法定位——`adb shell input text` 只支持 ASCII，而本应用的指令都是中文，插桩测试是唯一可靠的驱动方式。

### 改测试前必须知道的三件事

1. **`tests/conftest.py` 是套件的安全边界**：它在导入任何模块前把 `BANK_REQUIRE_SESSION=0`、`BANK_DEMO_MODE=1`、`DEEPSEEK_API_KEY=""`、以及两个**持久化路径**（`BANK_DEVICE_STORE_PATH` / `BANK_AUDIT_LOG_PATH`）置空。最后一条尤其重要：曾经因为 `.env` 里的设备库路径被测试继承，一次 `pytest` 把真实设备注册表覆盖成了测试夹具。需要持久化的用例要自己用 `monkeypatch.setenv` + `tmp_path` 显式申请，不要依赖开发者的 `.env`。
2. **日累计风控是跨请求累计的**：同一个核心进程里连续做敏感操作会不断升档。写涉及金额的测试要么用不同主体，要么显式重置状态。
3. **不要依赖在线模型**：需要模型参与时用 `monkeypatch.setattr(agent, "llm", ...)` 注入假实现；依赖真实模型的用例会随机失败。

### 提交前检查表

```powershell
python -m compileall -q .            # 语法
python -m pytest -q                  # 300 passed
python -m ruff check . --ignore E402 # 静态检查
docker compose config --quiet        # 编排文件仍然有效
```

涉及 Android 时再加 `.\gradlew.bat --no-daemon assembleDebug assembleDebugAndroidTest`。CI 会跑同样的命令；**只有在这些全过的情况下才提交**。

### 已知的坑

| 现象 | 原因与处理 |
| :--- | :--- |
| 模拟器连不上或应用"看起来没装" | 设备处于锁屏：未解锁时应用 Activity 不可见，`am start` 会报 `does not exist`。先解锁 |
| 红区流程走不下去 | 需要设备已设凭据（`adb shell locksettings set-pin 1234`），并用 `android/tools/satisfy-biometric-prompt.ps1` 应答 BiometricPrompt |
| 重启核心后推送静默失效 | 设备注册表默认只在内存。配 `BANK_DEVICE_STORE_PATH` 落盘，或让 App 重新登录 |
| Gradle 拉不到 Google Maven | 个别网络下 Java 直连 `dl.google.com` 会握手失败。把代理写在**用户级** `~/.gradle/gradle.properties`，不要提交进仓库 |
| 推送审计为 `NOT_SENT` | 先看 `/api/v1/security/health` 的 `push.configured` 与 `devices_with_tokens` 分别是真是假，再决定是配凭据还是重新登录设备 |

---

## 🧭 演示边界与诚实声明

本仓库是**可运行、可测试的竞赛演示系统**，不是生产银行系统。以下边界均与实际代码一致，答辩与验收时请按此口径表述：

| 边界 | 现状 |
| :--- | :--- |
| 数据与账本 | 全部为虚构演示数据；账本默认在进程内存（重启恢复初始 fixture），配置 `BANK_LEDGER_STORE_PATH` 后每笔已提交变更原子快照落盘并在启动时重放（完整性摘要配了 `BANK_AUTH_SECRET` 时为 HMAC，校验失败拒载并冻结写入）；任务队列仍在进程内存 |
| 身份认证 | 已有**真实登录**：`POST /api/v1/auth/login` 用 argon2id 校验口令（演示账户口令 `Bank@2026`），可要求短信 OTP，签发带 `jti` 的会话令牌；`BANK_REQUIRE_SESSION=1` 时写操作强制要求该令牌，且**会话主体覆盖客户端自称的 `X-User-Id`**；`POST /api/v1/auth/logout` 在服务端吊销**当前这一个**会话（登出不牵连其他设备）。生产仍需接 OIDC/统一认证 |
| 设备信任根 | 红区人脸因子由**设备自己的密钥**签名：App 在 Android Keystore 生成 P-256 密钥并注册公钥，服务端按公钥验签绑定 `jti` 的活体挑战（单次消费、60s 时效）。共享 HMAC 票据仅保留在 `BANK_DEMO_MODE` 下作演示捷径，非演示模式一律拒绝 |
| 推送 | 服务端到设备的投递链路是真实的（服务账号 → OAuth2 → FCM HTTP v1），但需要 `BANK_FCM_CREDENTIALS` 与客户端 `google-services.json`；缺任一项时**不假装成功**，健康端点报 `push.configured=false`，派发审计记 `NOT_SENT` 并附原因 |
| 设备注册表 | 设备公钥与推送令牌默认只在内存，可选落盘（`BANK_DEVICE_STORE_PATH`）；落盘文件带完整性摘要，配了 `BANK_AUTH_SECRET` 时为 HMAC（可防伪造），否则仅防损坏并在健康端点如实标注 |
| 短信 OTP | 演示模式为固定测试码（`BANK_MFA_TEST_OTP`）；关闭 `BANK_DEMO_MODE` 后改用 `BANK_MFA_OTP`，未接入真实短信通道 |
| 人脸因子 | 已从"布尔自证"升级为**挑战-签名一次性票据**（绑定交易 jti、单次消费、60s 时效）。Android 端由 Keystore 密钥签名，但仍是**软件级活体判定，不具备真实活体识别能力** |
| 分析沙箱 | AST 白名单 + 空 builtins + 短生命周期进程（受限环境降级为受限线程）+ 超时；非容器/microVM 级隔离，不可用时降级为本地等价聚合并标记 `analysis_engine` |
| 审计 | 进程内存哈希链 + 可选追加文件（重启按链校验回放）；Agent 侧事件（意图解析、回复校验）留在 Agent 进程，未与核心链路合并 |
| 并发与部署 | 正确性依赖单进程内的全局锁与快照回滚，**多 worker / 多副本部署不成立**；已有进程内每主体限流（`BANK_RATE_LIMIT_PER_MINUTE`），无 TLS/WAF |
| 行为熔断 | 规则与权重为确定性阈值（可用 `BANK_BEHAVIOR_*` 调整），基于**本进程内的行为时间线**；无设备指纹、地理位置、跨渠道画像等真实风控数据源 |
| 定时与代扣 | 到期任务停在 `WAITING_USER`，不自动扣款；订单草稿**不向真实商户下单**；订阅续费提醒已落任务中心（提前 2 天、幂等、取消订阅级联撤回） |
| AA 分账 | 在 Mock 账本内**真实收款**：参与人按分单位精确分摊扣款、发起人收款，尾差归发起人（100.00/3 → 33.33+33.33+33.34），参与人余额不足记 `PARTIAL` 并逐笔列明；真实支付渠道回款为扩展项 |
| 理财 | 风险测评、适合度校验、申赎与持仓为真实状态流转；年化收益与持仓估值为展示字段，**无净值/收益计算** |
| 卡片额度 | 卡片状态机（ACTIVE/FROZEN/LOST）与 `daily_limit` 均参与结算校验（超限 `CARD_LIMIT_EXCEEDED`，跨日重置） |
| 渠道网关 | 会话 checkpoint 落 SQLite（`BANK_CONVERSATION_DB`）；待审批句柄默认随其落盘（`<db>.approvals.json`，同款完整性摘要，`BANK_APPROVAL_STORE_PATH` 可重定位或设 `memory` 关闭），**网关重启后待审批仍可继续决策**；但网关与核心是**两个进程**，会话注册表以核心为准（网关通过 HTTP 回问核心，不自行维护副本） |

上述生产化整改项在 [`docs/security-self-assessment.md`](docs/security-self-assessment.md) 的 P0/P1/P2 清单中有对应条目与优先级。

---

## 📄 开源许可证与交付物索引

本项目基于 [MIT License](LICENSE) 协议开源。

- 作品简介（200 字）：[`docs/作品简介.md`](docs/作品简介.md)
- 架构设计说明书：[`docs/architecture.md`](docs/architecture.md)
- 安全设计与风险自评：[`docs/security-self-assessment.md`](docs/security-self-assessment.md)
- 赛题逐项验收审计表：[`docs/requirements-audit.md`](docs/requirements-audit.md)
- 5 分钟演示脚本：[`docs/demo-script.md`](docs/demo-script.md)
- 10 分钟答辩提纲：[`docs/答辩提纲.md`](docs/答辩提纲.md)
- 分步实施与验收指南：[`docs/implementation-guide.md`](docs/implementation-guide.md)
- 真机 / 模拟器验收手册：[`docs/device-verification.md`](docs/device-verification.md)
