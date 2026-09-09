# 赛题要求逐项验收表（演示沙箱）

本表把题目要求映射到可运行的模块和验收动作，避免把“已规划”误写成“已上线”。

| 题目要求 | 当前证据 | 验收方式 / 边界 |
| --- | --- | --- |
| 智能转账：姓名、手机号、备注 | `planner.py` 离线解析 + `main.py` 联系人白名单和转账流水 | 输入“给老婆转 300 元买菜”；核心重新解析收款人、金额和备注 |
| 定时转账 | `SCHEDULED_TRANSFER` 预检、未来时间规范化、任务中心 | 当前到期只进入 `WAITING_USER` 并暂停；生产/扩展版需补任务级重新授权结算端点 |
| AA 分摊 | `SPLIT_BILL` 以总额和参与人列表计算人均金额并生成可审计请求 | 至少一位参与人；参与人必须解析到 Core 联系人白名单；参与人通知/回款闭环需接入真实支付渠道 |
| 账单分析 | `filter_transactions` + `analyze_transactions` + 来源流水号 | 支持 TODAY/MONTH/LAST_MONTH/YEAR/ALL；模型不可补造金额或流水号 |
| 理财查询/推荐/风险评估/对比 | `/wealth/products`、`/wealth/recommend`、`/wealth/risk-assessment`、`/wealth/compare`；Agent 已接通评估与对比 | 查询/评估/对比绿区；推荐带免责声明；产品申购/赎回红区 MFA；申购前要求已有风险档案 |
| 卡片管理 | 查询、虚拟卡申请、额度、冻结/解冻、挂失/解挂（严格状态机） | 申请黄区；额度和安全状态变更红区 MFA |
| 密码修改 | `PASSWORD_CHANGE` 通过统一 operation gateway | 红区 OTP+人脸；Mock 只轮换凭证版本，不处理明文密码，生产交给 IdP |
| 订阅/代扣 | 列表、活跃扣费识别、续费提醒、取消 | 查询绿区；取消黄区确认；仅 Mock 数据 |
| 跨场景联动 | 生日锁资 + 依赖任务 + 生日前两天提醒/鲜花蛋糕订单草稿 | 生日日期不可解析时只澄清；`REMINDER.run_at` 必须等于事件前两天；订单草稿到期进入 `WAITING_USER`，不自动扣款；商户下单闭环为扩展项 |
| 权限分级 | Core `GENERIC_OPERATION_RISK` 与转账日累计策略 | 绿区自动、黄区确认、红区 OTP+人脸证明；策略不依赖前端布尔字段 |
| 幻觉防护 | 事实聚合、数值一致性和字段完整性检查 | 核心返回不完整/不一致时 Agent 明确“不声称成功” |
| 注入防御 | `sanitize_text` / `contains_prompt_injection` | 控制指令、危险代码和层级覆盖词进入拒绝/澄清分支 |
| 审计 | 结构化事件、脱敏、哈希链和验证接口 | `/api/v1/security/audit`（管理员）与 `/security/audit/verify` |
| 异常熔断 | 失败窗口、账户级锁定、`423` + Retry-After | 连续 MFA/token 失败触发安全锁定 |
| 沙箱 | AST 白名单、JSON 大小、超时、进程池 | 仅只读表达式；生产仍需容器/microVM/seccomp |
| DAG、中断、回退、人工接管 | `build_task_plan`、LangGraph interrupt、任务取消/接管、补偿字段 | 当前任务/账本为内存 Mock；正式环境需持久化工作流和事务 |

## 可重复验收

```powershell
python -m compileall -q .
python -m pytest -q
docker compose config --quiet
docker compose up --build -d
python scripts/smoke_test.py http://127.0.0.1:8000
```

当前仓库的演示账户、联系人、产品、交易和 MFA 因子均为虚构数据。正式上线前必须替换内存账本，接入 OIDC/设备绑定、真实 MFA、数据库事务、TLS/WAF/限流、KMS、消息队列及合规审查。报名材料中的 GitHub URL、PPT 和演示视频需由参赛团队补充真实地址和文件，不能把本地占位配置当作生产凭证。
