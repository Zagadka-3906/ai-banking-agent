# 从 Mock Core 到 Android/IM 参赛版：实施指南

> 当前仓库只交付 Android 原生客户端与签名 IM webhook；两者共享渠道网关、Agent 编排和 Mock Core。

## 1. 固定基线

1. 使用 Python 3.10–3.12 创建虚拟环境，安装 `requirements-dev.txt`。
2. 复制 `.env.example` 为 `.env`，本地使用虚构账户；共享环境注入随机 `BANK_AUTH_SECRET` 并保持 `BANK_REQUIRE_SESSION=1`。
3. 运行 Python、Compose 和 Android 构建检查，记录提交号与结果。
4. 不提交 `.env`、Firebase 服务账户、真实 IM secret、数据库文件或设备私钥。

## 2. 保持唯一记账边界

账户、流水、卡片、订阅和任务状态只在 `main.py` 变更。Android、IM、网关和 Agent 不能导入或直接修改账本对象。所有写操作走“预检 → 授权 → 执行”：

```text
POST /risk/precheck[/operation|/lock|/unlock]
POST /auth/confirm
POST /auth/mfa/face/challenge + /auth/mfa/verify  # RED
POST /transfers/execute 或 /operations/execute
```

执行端再次验证主体、会话、业务参数、账户/卡状态、余额、风险、TTL、单次性和幂等键。客户端字段、模型文字和 IM 消息都不能直接授权。

## 3. 集中策略

`planner.py` 只允许 `Intent` 白名单并映射 GREEN/YELLOW/RED；`main.py` 用服务端日累计和状态计算最终风险。新增场景时同时增加意图、槽位 Schema、Core 预检、执行校验、审计事件和负向测试，不能只加对话文案。

## 4. 建立真实会话和设备验证

Android 必须通过 `/auth/login` 获取会话。网关收到 `X-Session-Token` 后回 Core 校验，不能信任裸 `X-User-Id`。红区设备因子使用 Android Keystore P-256 密钥：登录后登记公钥，BiometricPrompt 后对 Core 一次性 challenge 签名。服务端不接受 `face_verified` 布尔值或共享客户端密钥。

IM 只能绑定一个已登录会话。Provider webhook 必须验证 HMAC、时间窗和事件编号；事件体自带的用户标识一律忽略。确认和 MFA 回 Android/API 完成。

## 5. 编排复杂任务

`agent.py` 的基本路径为解析、校验、风险检查、`interrupt()`、执行和事实回复。`build_task_plan()` 将生日锁资、预约和 AA 生成依赖 DAG；`scheduler.py` 保存状态、幂等键、重试预算与补偿。到期任务进入 `WAITING_USER`，重新授权后才能结算。

模糊表达和省略回答通过会话上下文补槽，但不得继承旧授权。写操作存在歧义时继续澄清；用户改说完整新需求时终止旧澄清并处理新意图。

## 6. 扩展业务

- 转账：联系人姓名、别名、手机号、备注、预约与 AA。
- 账单：日、月、上月、年和全部范围，分类与异常引用具体流水。
- 理财：风险测评、推荐/对比免责声明、申购/赎回红区授权。
- 卡片：查询、申请、调额、冻结、解冻、挂失和解挂，严格限制状态转移。
- 订阅：识别活跃扣费、续费提醒与确认取消。
- 跨场景：日期、预算、锁资、提醒和订单草稿建立显式依赖。

Agent 回复必须从 Core 返回字段生成。响应异常或无法核验时明确说明未完成，不能合成流水号、余额、持仓、卡状态或订阅结果。

## 7. Android 与 IM 交付

Android 界面位于 `android/app/src/main/java/com/aibanking/agent/`。新增交互控件应设置稳定 `testTag`，并补充 Compose 单元/插桩测试。网络层要保留唯一 SSE 终态、登出吊销、会话过期回登录页和错误降级。

IM 适配器位于 `im_webhook.py`。新增 provider 时只扩展部署配置与协议适配，不复制身份和授权逻辑。绑定、去重和会话状态在生产环境必须迁移到共享持久化存储。

`sandbox.py` 只允许受限只读表达式；生产代码执行还需要独立容器或 microVM、只读文件系统、无网络命名空间、资源配额和集中监控。

## 8. 验收与交付

```powershell
.\.venv\Scripts\python.exe -m compileall -q main.py agent_api.py agent.py conversation.py planner.py security.py sandbox.py scripts tests
.\.venv\Scripts\python.exe -m ruff check . --ignore E402
.\.venv\Scripts\python.exe -m pytest -q
docker compose config --quiet
cd android
.\gradlew.bat --no-daemon :app:testDebugUnitTest :app:assembleDebug :app:assembleDebugAndroidTest
```

随后运行根目录 `start-emulator.bat`，确认脚本从当前源码构建、覆盖安装并打开应用。按 [设备验收](device-verification.md) 完成登录、查询、Yellow 确认、Red 设备签名、登出和推送验证；按 IM 测试覆盖绑定、签名、过期、重放、未知 provider 与审批回流。

参赛交付同时维护 [架构说明](architecture.md)、[安全自评](security-self-assessment.md)、[自然语言评测](agent-natural-language-evaluation.md)、[安全评测](agent-security-evaluation.md)、[演示脚本](demo-script.md) 和 [答辩提纲](答辩提纲.md)。

当前账本与 provider 都是 Mock。生产化需要事务数据库、Redis、OIDC、真实 MFA、TLS/WAF、消息队列、KMS、真实支付/IM provider 与合规审查。
