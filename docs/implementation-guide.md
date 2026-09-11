# 从基础 Demo 到参赛版：分步实施指南

本文记录本仓库的推荐改造顺序。每一步都先建立可验证的边界，再扩展业务，避免把大模型直接接到记账函数。

## 1. 先固定运行基线

1. 使用 Python 3.10–3.12 创建虚拟环境。
2. 安装 `requirements.txt` 和 `requirements-dev.txt`。
3. 复制 `.env.example` 为 `.env`，本地演示使用 `BANK_DEMO_MODE=1`；共享环境打开 `BANK_REQUIRE_CLIENT_AUTH=1`，并替换随机 `BANK_AUTH_SECRET`。
4. 先运行 `python -m compileall -q .` 和 `python -m pytest -q`，记录基线结果。

## 2. 建立唯一记账边界

在 `main.py` 保留账户、流水和余额变更，Agent 不直接导入这些对象。所有写操作按“预检 → 授权 → 执行”三段式接口走：

```text
POST /risk/precheck[/operation|/lock]
POST /auth/confirm                 # 黄色
POST /auth/mfa/verify              # 红色额外步骤
POST /transfers/execute 或 /operations/execute
```

执行端再次校验用户、账户状态、目标、金额、余额、幂等键和凭证，不能把浏览器的布尔字段当作授权。

## 3. 把权限策略集中化

`planner.py` 只允许 `Intent` 白名单，并将意图映射为 GREEN/YELLOW/RED。`main.py` 再用日累计和服务端状态计算最终风险：转账累计不超过 1000 元为黄色，超过 1000 元为红色；大额锁资/解锁同样升级红区。新增业务时先加策略表、预检字段和测试，再开放执行路由。

## 4. 加入安全控制

`security.py` 提供 HMAC 短时效一次性 nonce、金额/收款人/操作上下文绑定、一次性 MFA proof、审计哈希链、敏感字段脱敏和账户级熔断。输入先经过 `sanitize_text`；模型只能返回结构化意图，不能返回可执行 Python 或任意工具名。

## 5. 编排复杂任务

`agent.py` 使用 LangGraph：解析 → 风险检查 → `interrupt()` 人工确认 → 执行 → 事实型回复。`build_task_plan()` 为生日锁资、预约转账和 AA 分摊生成依赖 DAG；`scheduler.py` 保存任务状态、幂等键、重试预算和补偿动作。取消、失败或人工接管必须停止后续节点，资金解锁走独立的黄色/红色预检流程；预约和订单到期在演示版停在 `WAITING_USER`，扩展版再接任务级结算 worker。

## 6. 扩展业务场景

- 转账：联系人姓名/别名/手机号、备注、预约和 AA。
- 账单：TODAY、MONTH、LAST_MONTH、YEAR、ALL，分类和异常流水引用。
- 理财：产品事实、风险评估、推荐/对比免责声明、申购/赎回红区；申购前检查风险档案。
- 卡片：查询、虚拟卡申请、调额、冻结/解冻、挂失/解挂，并在 Core 执行严格状态转移。
- 密码安全：`PASSWORD_CHANGE` 通过红区 MFA；演示只轮换凭证版本，不把明文密码送入 Agent。
- 订阅：识别活跃扣费、续费提醒、取消确认。
- 跨场景：生日日期必须明确；锁资成功后创建生日前两天提醒和鲜花/蛋糕订单草稿。

每个新场景都要同时补充：意图白名单、Core 预检/执行、Agent 回复字段校验、审计事件和回归测试。

## 7. 前端与沙箱

`web_app.py` 只展示确认卡和 MFA 表单，凭证由 Core 签发/校验。`sandbox.py` 只接受 AST 白名单的只读表达式，限制节点、整数幂、输入输出大小和超时；生产环境还必须放入隔离容器或 microVM，禁止网络和写文件。

## 8. 验收与交付

```powershell
python -m compileall -q .
python -m pytest -q
git diff --check
docker compose config --quiet
docker compose up --build -d
python scripts/smoke_test.py http://127.0.0.1:8000
docker compose down
```

参赛提交时附上 `作品简介.md`、`architecture.md`、`security-self-assessment.md`、演示脚本和答辩提纲，并由团队补填真实 GitHub 地址、PPT 和录屏视频。Android 端的真机/模拟器验收步骤（按需启动模拟器、设备凭据、插桩测试、通过标准）见 `device-verification.md`。当前仓库仍是虚构数据的内存 Mock；生产化必须迁移数据库/Redis、OIDC、真实 MFA、TLS/WAF、限流、消息队列、KMS 和合规审查。
