# 赛题要求逐项验收表（演示沙箱）

本表把题目要求映射到可运行的模块和验收动作，避免把“已规划”误写成“已上线”。

| 题目要求 | 当前证据 | 验收方式 / 边界 |
| --- | --- | --- |
| 智能转账：姓名、手机号、备注 | `planner.py` 离线解析 + `main.py` 内置白名单、**联系人簿**（姓名/电话/关系/生日，App 可增删；家人细分爱人/兄弟/儿子/女儿/父母）和转账流水 | 输入“给老婆转 300 元买菜”；新增联系人后“给王五转 300 元”同样结算（电话命中既有账户即关联，否则开立零余额影子收款账户，口令摘要不可用）；**亲属称呼按关系解析**：“给我爱人转 200”落到联系人簿中关系为爱人的联系人（簿内无此关系回退内置白名单；多个同关系在预检即拒绝并列出姓名）；**首次咨询理财自动触发 8 题风险测评（C1-C5）**：答案由服务端评分定档并存档，用户只见类型/特点/匹配级别与匹配产品（得分不展示），完成前申购类意图被问卷拦截；核心重新解析收款人、金额和备注 |
| 定时转账 | `SCHEDULED_TRANSFER` 预检、未来时间规范化、任务中心 | 当前到期只进入 `WAITING_USER` 并暂停；生产/扩展版需补任务级重新授权结算端点 |
| AA 分摊 | `SPLIT_BILL` 以参与人列表精确分摊（分单位取整，尾差归发起人）并在 Mock 账本内真实收款 | 参与人须解析到 Core 联系人；参与人余额不足记 `PARTIAL` 并逐笔列明，绝不静默扣款；真实支付渠道回款为扩展项 |
| 账单分析 | `filter_transactions` + `analyze_transactions`（沙箱执行的类目聚合）+ 来源流水号 | 支持 TODAY/MONTH/LAST_MONTH/YEAR/ALL；退款单列、深夜规则按业务时区；模型不可补造金额或流水号 |
| 理财查询/推荐/风险评估/对比 | `/wealth/products`、`/wealth/recommend`、`/wealth/risk-assessment`、`/wealth/compare`；Agent 已接通评估与对比并渲染为可读清单 | 查询（在售清单）与推荐（按风险偏好过滤）为不同意图，不会把清单静默收窄；推荐带免责声明；产品申购/赎回红区 MFA；申购前要求已有风险档案；年化收益与持仓估值为展示字段（无净值/收益引擎） |
| 卡片管理 | 查询、虚拟卡申请、额度、冻结/解冻、挂失/解挂（严格状态机）；`daily_limit` 参与结算限额校验 | 申请黄区；额度和安全状态变更红区 MFA；超限返回 `CARD_LIMIT_EXCEEDED`，跨日自动重置。**限额对所有出金通道生效**（转账与理财申购走同一张付款卡、同一计数器）；日限额有独立上限（`BANK_MAX_CARD_DAILY_LIMIT`，默认 20 万），不受单笔转账上限约束；持卡上限对 `/cards/{id}/add` 与 `CARD_APPLY` 同时生效 |
| 联系人簿与 AA 分摊 | 姓名/电话/关系/生日，App 可增删；AA 分摊真实扣参与者账户 | 只凭输入手机号建立的关联为**临时**关联：可付款、不可代扣（AA 返回 `PARTICIPANT_NOT_AUTHORIZED`），且不回显号主真实姓名；双方各自把对方号码录入自己的联系人簿后才成为已确认联系人 |
| 密码修改 | `PASSWORD_CHANGE` 通过统一 operation gateway | 红区 OTP+人脸票据；Mock 只轮换凭证版本，不处理明文密码，生产交给 IdP |
| 订阅/代扣 | 列表、活跃扣费识别、续费提醒落任务中心（提前 2 天、幂等）、取消 | 查询绿区；提醒创建为可审计写操作；取消订阅级联撤回其待办提醒；仅 Mock 数据 |
| 跨场景联动 | 生日锁资 + 依赖任务 + 生日前两天提醒/鲜花蛋糕订单草稿 | 生日日期不可解析时只澄清；`REMINDER.run_at` 必须等于事件前两天；订单草稿到期进入 `WAITING_USER`，不自动扣款；商户下单闭环为扩展项 |
| 意图理解与多轮 | 离线规则解析 + LLM 槽位抽取 + 隐含意图映射表 + `missing_slots` 定向澄清 + `context.pending_clarification` 回填 + 回指解析 | 模糊/省略表达须落在白名单意图；隐含说法（“钱都花哪儿去了”“卡被盗刷”“不想被扣会员费”）映射到具体操作；澄清后的裸答按槽位类型解释；本地解析到的日期/金额/收款人优先于模型输出；**一句话多个只读诉求拆解为有序 DAG 依次执行**，含变更类子请求时提示逐条办理；不猜测缺失槽位 |
| 权限分级 | Core `GENERIC_OPERATION_RISK` 与转账日累计策略 | 绿区自动、黄区确认、红区 OTP+挑战-签名人脸票据；策略不依赖前端布尔字段；写操作可用会话令牌替代自称身份（`BANK_REQUIRE_SESSION=1` 强制） |
| 幻觉防护 | 事实聚合、数值一致性和字段完整性检查 | 核心返回不完整/不一致时 Agent 明确“不声称成功” |
| 注入防御 | `sanitize_text` / `contains_prompt_injection` | 控制指令、危险代码和层级覆盖词进入拒绝/澄清分支 |
| 审计 | 结构化事件、脱敏、哈希链、验证接口与决策链路还原 | `/api/v1/security/audit`（本人/管理员）、`/security/audit/verify`（管理员）、`/security/audit/trace/{request_id}`（本人或管理员）；配置 `BANK_AUDIT_LOG_PATH` 时重启按链校验回放 |
| 异常熔断 | 失败窗口、账户级锁定、`423` + Retry-After；**行为组合熔断**（信号加权 + 6 条命名规则 + 滑窗评分） | 连续 MFA/token 失败或命中组合规则（大额转账后挂失/改密、调额后大额转出、连续大额转账/卡片变更、失败后成功）即锁定；`/security/behavior/{user_id}` 返回评分与命中规则，阈值与窗口可用 `BANK_BEHAVIOR_*` 调整 |
| 沙箱 | AST 白名单、JSON 大小、超时、进程池（受限环境降级受限线程） | 账单类目聚合已真实经沙箱执行并标记 `analysis_engine`；仅只读表达式，生产仍需容器/microVM/seccomp |
| DAG、中断、回退、人工接管 | `build_task_plan` + `PlanExecutor` 依赖顺序推进、LangGraph interrupt、任务取消/接管、失败补偿核对（UNLOCK_FUND/ROLLBACK_WEALTH） | 计划账本如实记录每个步骤；由核心任务中心接管的步骤保持 PENDING；正式环境需持久化工作流和事务 |

## 可重复验收

```powershell
python -m compileall -q .
python -m pytest -q
docker compose config --quiet
docker compose up --build -d
python scripts/smoke_test.py http://127.0.0.1:8000
```

当前仓库的演示账户、联系人、产品、交易和 MFA 因子均为虚构数据。核心账本、待审批注册表、设备注册表和审计链均已支持可选落盘（HMAC 完整性校验、篡改即拒载并冻结写入，见 `.env.example` 的 `BANK_LEDGER_STORE_PATH` / `BANK_APPROVAL_STORE_PATH` / `BANK_DEVICE_STORE_PATH` / `BANK_AUDIT_LOG_PATH`），重启不再丢失已提交状态；但运行时仍是单进程沙箱，正式上线前必须替换为数据库事务，并接入 OIDC/设备绑定、真实 MFA、TLS/WAF/限流、KMS、消息队列及合规审查。报名材料中的 GitHub URL、PPT 和演示视频需由参赛团队补充真实地址和文件，不能把本地占位配置当作生产凭证。
