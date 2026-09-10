# 🏦 AI Banking Agent (银行 AI 智能体沙箱系统)

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/Orchestration-LangGraph-orange.svg)](https://github.com/langchain-ai/langgraph)
[![Backend](https://img.shields.io/badge/Backend-FastAPI-green.svg)](https://fastapi.tiangolo.com/)
[![Frontend](https://img.shields.io/badge/Frontend-Streamlit-red.svg)](https://streamlit.io/)
[![License](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

基于 **LangGraph 确定性状态机**、**受限核心银行沙箱 (Mock Core Banking)** 与 **DeepSeek 大模型** 构建的受控银行 AI 智能体系统（代号“安言”）。

本项目针对银行业务的高安全、严风控需求设计，严格践行 **“决策与执行物理分离”** 与 **“低风险自动、高风险确认、极高风险强验证”** 的权限分级理念。大模型仅负责自然语言意图理解与任务 DAG 规划，账户变更由独立的确定性 Mock Core Banking API 统一执行，彻底杜绝模型幻觉与越权写账。

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
  - **网关入口控制**：会话令牌（`POST /auth/session`，可 `BANK_REQUIRE_SESSION=1` 强制写操作使用，且会话主体覆盖客户端自称身份）+ 每主体请求窗口限流（超限 429 + Retry-After，探针豁免）；
  - **审计哈希链与决策链路**：全量关键操作生成结构化审计事件并计算 Hash-chain，支持防篡改自检验证；同一次业务请求由 `request_id` 串联为完整决策链（`/api/v1/security/audit/trace/{request_id}`：预检 → 确认 → MFA → 结算 → 凭证消费），配置 `BANK_AUDIT_LOG_PATH` 时审计文件在重启后按链校验回放续链。

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
├── planner.py           # 意图白名单、槽位提取、风险路由映射、跨场景任务 DAG 与事实聚合
├── security.py          # HMAC 凭证签名、MFA 校验、审计哈希链、账户熔断与输入注入防御
├── scheduler.py         # 线程安全任务中心（定时转账/代扣提醒/订单草稿/人工接管队列）
├── sandbox.py           # 受限只读表达式沙箱（AST 白名单 + 进程/线程隔离 + 超时），账单聚合经此执行
├── bank_tools.py        # 独立 HTTP 传输适配层（供外部集成方与旧静态调用兼容；主链路 agent.py 自持 _http）
├── web_app.py           # Streamlit 前端交互系统（支持卡片挂起、MFA 模态弹窗与实时看板）
├── tests/               # 自动化测试套件（9 个文件 / 164 个用例）
│   ├── test_core_api.py             # 核心契约、金额与风险边界、状态/卡片拒绝路径
│   ├── test_security_regressions.py # 身份、票据绑定、上下文篡改、审计、回滚
│   ├── test_concurrency.py          # 并发幂等、同一 nonce 单次记账、并发锁资
│   ├── test_gateway_controls.py     # 会话令牌、身份覆盖、限流 429 与豁免
│   ├── test_behavior_circuit.py     # 行为组合熔断：规则触发、阈值可配、锁定与恢复
│   ├── test_agent_workflow.py       # LangGraph 中断/恢复、DAG 账本、补偿、幂等重试
│   ├── test_planner.py              # 意图白名单、风险阈值、账单事实、计划执行器
│   ├── test_security_primitives.py  # 凭证签名、注入检测、审计链与持久化回放
│   └── test_extended_scenarios.py   # 理财/卡片/订阅/AA 收款/任务接管与链路还原
├── scripts/
│   └── smoke_test.py    # 容器冒烟验收（/healthz + 余额可读且非负）
├── .github/workflows/
│   └── ci.yml           # CI：compileall + pytest + ruff + docker compose config
├── docs/                # 架构设计、安全自评报告、赛题验收表、演示脚本与答辩提纲
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
git clone [https://github.com/your-org/ai-banking-agent.git](https://github.com/your-org/ai-banking-agent.git)
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
# 必须配置为长随机字符串，用于 HMAC 签名
BANK_AUTH_SECRET=your_super_secret_key_change_in_production
BANK_DEMO_MODE=1
BANK_MFA_TEST_OTP=888888
# 人脸票据密钥：模拟设备 SDK 用它对服务端挑战签名（生产必须随机且隔离）
BANK_FACE_ATTEST_SECRET=demo-face-attest-secret-change-me
BANK_REQUIRE_CLIENT_AUTH=0
# 网关控制：会话令牌（1 = 写操作强制要求 POST /auth/session 返回的令牌）与限流窗口
BANK_REQUIRE_SESSION=0
BANK_RATE_LIMIT_PER_MINUTE=240
BANK_SESSION_TTL_SECONDS=900
BANK_API_BASE=[http://127.0.0.1:8000/api/v1](http://127.0.0.1:8000/api/v1)
DEEPSEEK_API_KEY=your_deepseek_api_key_here
# 可选：审计追加文件路径；配置后重启会按哈希链校验回放续链
BANK_AUDIT_LOG_PATH=
# 可选：审计管理员令牌；同时决定 /tasks/run-due 是否要求管理员身份
BANK_ADMIN_AUDIT_TOKEN=
```

> **安全须知**：共享演示或容器部署时，建议设置 `BANK_DEMO_MODE=0`、`BANK_REQUIRE_CLIENT_AUTH=1` 并注入随机的 `BANK_AUTH_SECRET` / `BANK_FACE_ATTEST_SECRET`。本地演示即使关闭严格鉴权，所有写接口与确认/MFA 接口仍必须携带 `X-User-Id`。若未配置在线模型密钥，系统将自动平滑降级为确定性离线规则解析引擎。

---

## 🖥️ 运行与交互演示

系统采用前后端解耦架构，请**在两个独立终端窗口**分别启动服务：

### 终端 1：启动 Mock 核心银行沙箱 (Port: 8000)

```powershell
python -m uvicorn main:app --reload --port 8000
```
> 启动后可直接访问 Swagger 可视化文档进行接口自测：`http://127.0.0.1:8000/docs`；健康检查：`http://127.0.0.1:8000/healthz`。

### 终端 2：启动 Streamlit 智能体交互前端 (Port: 8501)

```powershell
python -m streamlit run web_app.py
```
> 启动后浏览器将自动打开交互界面：`http://127.0.0.1:8501`。

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

```powershell
# 切换至离线规则解析模式，排除网络与在线模型干扰
$env:DEEPSEEK_API_KEY = ''

# 运行全部测试（契约、安全回归、Agent 工作流、规划、安全原语、扩展场景、并发原子性、网关控制、行为熔断）
python -m pytest -q

# 也可按专项运行（tests/ 共 9 个文件）
python -m pytest -q tests/test_core_api.py           # 核心契约与金额/风险边界
python -m pytest -q tests/test_security_regressions.py  # 安全攻击与回滚回归
python -m pytest -q tests/test_concurrency.py        # 并发幂等与单次记账
python -m pytest -q tests/test_gateway_controls.py   # 会话令牌与限流
python -m pytest -q tests/test_behavior_circuit.py   # 行为组合熔断

# 静态语法编译检查
python -m compileall -q .

# 容器化构建与冒烟验收
docker compose up --build -d
python scripts/smoke_test.py [http://127.0.0.1:8000](http://127.0.0.1:8000)
docker compose down
```

---

## 🧭 演示边界与诚实声明

本仓库是**可运行、可测试的竞赛演示系统**，不是生产银行系统。以下边界均与实际代码一致，答辩与验收时请按此口径表述：

| 边界 | 现状 |
| :--- | :--- |
| 数据与账本 | 全部为虚构演示数据，保存在进程内存，重启即恢复初始 fixture |
| 身份认证 | 默认 `X-User-Id` 为自称主体（无登录/口令）；已提供 `POST /auth/session` 会话令牌，设置 `BANK_REQUIRE_SESSION=1` 后写操作强制要求会话令牌。生产仍需 OIDC 与设备绑定 |
| 短信 OTP | 演示模式为固定测试码（`BANK_MFA_TEST_OTP`）；关闭 `BANK_DEMO_MODE` 后改用 `BANK_MFA_OTP` |
| 人脸因子 | 已从"布尔自证"升级为**挑战-签名一次性票据**（绑定交易 jti、单次消费、60s 时效），但签名方是模拟设备 SDK，**不具备真实活体识别能力** |
| 分析沙箱 | AST 白名单 + 空 builtins + 短生命周期进程（受限环境降级为受限线程）+ 超时；非容器/microVM 级隔离，不可用时降级为本地等价聚合并标记 `analysis_engine` |
| 审计 | 进程内存哈希链 + 可选追加文件（重启按链校验回放）；Agent 侧事件（意图解析、回复校验）留在 Agent 进程，未与核心链路合并 |
| 并发与部署 | 正确性依赖单进程内的全局锁与快照回滚，**多 worker / 多副本部署不成立**；已有进程内每主体限流（`BANK_RATE_LIMIT_PER_MINUTE`），无 TLS/WAF |
| 行为熔断 | 规则与权重为确定性阈值（可用 `BANK_BEHAVIOR_*` 调整），基于**本进程内的行为时间线**；无设备指纹、地理位置、跨渠道画像等真实风控数据源 |
| 定时与代扣 | 到期任务停在 `WAITING_USER`，不自动扣款；订单草稿**不向真实商户下单**；订阅续费提醒已落任务中心（提前 2 天、幂等、取消订阅级联撤回） |
| AA 分账 | 在 Mock 账本内**真实收款**：参与人按分单位精确分摊扣款、发起人收款，尾差归发起人（100.00/3 → 33.33+33.33+33.34），参与人余额不足记 `PARTIAL` 并逐笔列明；真实支付渠道回款为扩展项 |
| 理财 | 风险测评、适合度校验、申赎与持仓为真实状态流转；年化收益与持仓估值为展示字段，**无净值/收益计算** |
| 卡片额度 | 卡片状态机（ACTIVE/FROZEN/LOST）与 `daily_limit` 均参与结算校验（超限 `CARD_LIMIT_EXCEEDED`，跨日重置） |

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
