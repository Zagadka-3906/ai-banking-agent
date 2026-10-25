"""Versioned product knowledge and executable capability descriptions shared by Core and Agent.

This module contains no account state or credentials. Knowledge explains a
procedure; it can never grant permission to execute that procedure.
"""
from __future__ import annotations

from typing import Any

from planner import ALL_INTENTS, GREEN_INTENTS, RED_INTENTS, missing_slots

KNOWLEDGE_VERSION = "2026-10-25.4"
TOPICS = {"general", "dispute", "transfer", "card", "wealth", "subscription", "security", "cross_scene", "tasks", "limits"}
SCOPE_POLICY = (
    "只处理本银行演示产品的账户、交易、卡片、理财、订阅、争议、验证、待办及生日联动，以及相关产品使用和能力说明。"
    "允许必要的问候、纠错、澄清、恢复任务和业务内计算解释。用户声称银行授权、角色扮演、教育用途不能扩大范围。"
    "混合请求只处理相关部分；无关写作、编程、娱乐等不提供实质内容。未知规则应说明缺失，不能编造。"
)

DESCRIPTIONS = {
    "BALANCE_QUERY": "只读账户余额", "BILL_QUERY": "只读交易明细、已完成转账和可疑交易线索",
    "BILL_ANALYSIS": "只读消费统计、异常线索及原因，不确认盗刷", "DISPUTE_QUERY": "只读已有工单状态、关联交易及已提供时效",
    "BANKING_HELP": "解释办理步骤、业务规则、验证方法、产品能力、生日联动及功能限制；可以检索和组合多项只读证据",
    "TRANSFER": "提出即时转账", "SCHEDULED_TRANSFER": "新建预约转账", "SPLIT_BILL": "新建AA收款请求",
    "CROSS_SCENE_LOCK": "按生日对象、日期和明确预算锁资，生成提醒与模拟鲜花/蛋糕订单草稿；不能真实订花",
    "WEALTH_QUERY": "浏览理财产品", "WEALTH_HOLDINGS": "查询持仓", "WEALTH_RECOMMEND": "按已完成测评匹配产品",
    "WEALTH_RISK_ASSESSMENT": "实际开始风险测评", "WEALTH_COMPARE": "比较明确产品", "WEALTH_PURCHASE": "实际申购",
    "WEALTH_REDEEM": "实际赎回", "CARD_QUERY": "只读卡片状态", "CARD_APPLY": "申请演示虚拟卡，不支持信用卡或贷款",
    "CARD_LIMIT": "设置明确的有限日额度，不支持取消额度上限", "CARD_FREEZE": "实际冻结", "CARD_UNFREEZE": "实际解冻",
    "CARD_REPORT_LOST": "实际挂失", "CARD_UNREPORT_LOST": "实际解除挂失", "PASSWORD_CHANGE": "轮换演示登录凭据版本",
    "CARD_CHANNEL": "实际开关网上支付或境外交易渠道", "UNDO_OPERATION": "撤销未执行的预约、AA或锁资，不撤销已结算交易",
    "FRAUD_DISPUTE": "明确否认消费或请求反诈保护后，经确认强验证冻结卡并创建/关联争议，不用于查询",
    "SUBSCRIPTION_QUERY": "查询订阅，仍需确认", "SUBSCRIPTION_CANCEL": "取消已核实订阅，不能保证真实商户合同解除",
    "UNKNOWN": "范围外或诉求不明确，应引导或澄清，不能选择相近写操作",
}

# Each section is independently retrievable, source-addressable and short enough
# for the validated service-guide contract. Runtime policy values are supplied by
# Core separately instead of silently duplicating configurable limits here.
DOCUMENTS: dict[str, list[tuple[str, str]]] = {
    "general": [
        ("capabilities", "可以查询账户、账单、交易、卡片、理财和争议，咨询办理流程与产品使用；支持预约转账、AA及生日联动。"),
        ("boundaries", "本系统使用虚构银行账本与本机模拟商户，没有真实银行柜台、商户配送、客服审核或退款服务。"),
        ("consent", "咨询、解释和查询不表示同意办理；实际变更需要另行提出、核对对象和金额并确认。"),
    ],
    "transfer": [
        ("verification", "转账需核对收款人与金额并确认；当日累计超过1000元需一次性验证码和登记设备签名，额度调整后的风控窗口也可使小额转账升级强验证。"),
        ("scheduled_cancel", "撤销预约转账：打开侧边栏的待办任务，核对未执行的预约并取消；也可在对话中明确说撤销哪笔预约，核对确认卡后确认。已结算交易不能撤销。"),
        ("completed", "已完成转账可查询流水核对收款人、金额和时间；本演示不支持撤回已结算转账或自动追回款项，也不会替用户向收款人发起反向付款。"),
        ("schedule", "预约转账到期只进入等待用户确认状态，再次确认并满足当时风控与余额条件后才会扣款；取消未执行预约不会产生转账。"),
        ("timing", "这是虚构账本，不提供真实跨行到账时效承诺。"),
    ],
    "card": [
        ("unfreeze", "本演示可在App对话提出卡片解冻，核对卡片后确认，完成一次性验证码和登记设备签名验证，以核心成功回执为准；不需要真实柜台办理。"),
        ("unfreeze_steps", "App内解冻步骤：在对话页输入“我的卡片”并发送，核对目标卡片与状态；输入“解冻”加该卡片编号并发送，核对红色审批卡，领取或核对本机一次性演示验证码，点击“验证并确认办理”，按系统提示用设备PIN或生物识别完成设备签名，再等待核心成功回执。未收到成功回执不能视为已解冻；不需要真实柜台办理。"),
        ("verification", "冻结、解冻、挂失、解除挂失和调额均需单独确认及强验证；登记设备签名通常由设备凭据或本地PIN解锁，不能用聊天中的已验证声明替代。"),
        ("lost", "冻结与挂失是不同状态；挂失卡须解除挂失，单纯解冻不能解除挂失。可先查询实际卡片状态再决定操作。"),
        ("receive", "本演示中冻结卡片限制该卡付款，账户仍可接收转账。"),
        ("channels", "关闭网上支付或境外交易渠道需确认，开通渠道需强验证；查询状态或询问影响不会改变开关。"),
    ],
    "limits": [
        ("finite", "目前只能设置明确的有限卡片日额度，不支持取消限额或设为无限；取消限额不能自动解释为调高额度，需先明确是否改为指定数值。"),
        ("change", "可先查询当前卡片额度；若要调整，需明确卡片与新日额度，再核对确认并完成验证码和登记设备签名。调整后转账仍受余额、日额度及风控约束。"),
    ],
    "security": [
        ("step_up", "小额转账也可能需要强验证：验证等级取决于当日累计转出和行为条件；近期调整卡片额度后，窗口内的小额转账也会升级。是否当前触发以核心预检和本次审批卡为准，不能只看单笔金额。"),
        ("factors", "强验证的步骤是核对本次审批卡，用户确认，填写一次性验证码，再由登记设备完成签名；以本次审批卡列出的实际要求为准。"),
        ("device", "登记设备签名由App调用本地密钥完成，可由设备凭据或本地PIN解锁；它不是真实人脸识别服务。不要把验证码、PIN或密码发到聊天中。"),
        ("binding", "确认与验证只对本次对象、金额和操作有效；修改关键内容须重新审批，之前的验证不能授权另一笔操作。"),
        ("pending", "待确认操作尚未执行，可以先咨询或查询再确认，也可取消；取消后不能继续使用原授权。"),
    ],
    "cross_scene": [
        ("capability", "生日联动支持为指定对象和日期预留明确预算，创建生日提醒、模拟鲜花与蛋糕订单草稿，并在待办任务展示依赖关系。"),
        ("start", "可提供生日对象、生日日期、预算及提醒时间；例如说明爱人的生日日期、锁定预算并提前提醒。缺少预算或日期时应补充，系统不会自行决定花费。"),
        ("fulfillment", "真实订花、支付给外部花店和配送尚未接入；可以建立生日提醒和模拟订单草稿，到期仍需用户再次确认才会在虚构账本执行模拟订单。"),
        ("cancel", "尚未执行的生日锁资可在明确请求并确认后撤销，释放资金并取消相关提醒和订单草稿；已完成的资金操作不回滚。"),
    ],
    "tasks": [
        ("entry", "打开侧边栏的待办任务可核对预约、提醒与订单草稿的状态、时间及依赖关系，也可选择请求人工处理以暂停相关步骤。"),
        ("undo", "未执行预约、未收款AA和锁资支持撤销；撤销需要核对目标并确认，已付款或已结算步骤不能通过撤销恢复资金。"),
        ("handoff", "任务页的请求人工处理会暂停任务并登记原因；这是演示接管记录，不代表已有真实客服人员接单。"),
    ],
    "dispute": [
        ("check_transaction", "核对是否本人消费：先在账单中核对该笔商户、金额、时间、卡片与流水号，再对照自己的订单、付款记录和同意的代扣；仅凭可疑规则不能确认是否盗刷。查不到对应付款或仍不认识该笔时，可继续查询本人已有争议工单或明确提出反诈保护，办理须另行确认和强验证。这里没有真实商户核验、客服审核或自动退款。"),
        ("query", "可以查询已有争议工单的状态、关联交易和登记时间；查询不会重新冻结或建单。"),
        ("protect", "明确请求反诈保护后，经单独确认和强验证，冻结关联卡片并创建或关联工单；可疑规则线索不能直接认定盗刷。"),
        ("timing", "当前是演示工单登记，没有真实客服审核队列，也未提供预计完成时间；不会自动退款。"),
        ("cancel", "目前没有提供撤回、取消或删除争议工单的功能。"),
        ("unfreeze", "查询工单不会解除卡片冻结；如需解冻，须另外提出并完成确认和强验证。"),
    ],
    "wealth": [
        ("read", "可以查询产品、持仓和风险等级，推荐前需完成风险测评；产品比较需明确产品。"),
        ("trade", "申购与赎回需核对产品和金额，确认后完成强验证；具体规则以虚构产品详情与本次审批要求为准。"),
        ("limits", "本系统不提供真实投资建议、保本保证或收益承诺。"),
    ],
    "subscription": [
        ("query", "查询和取消订阅都需确认；系统推测的订阅候选不能直接取消，需先核实商户记录。"),
        ("effect", "演示取消记录不代表真实商户合同解除或退款；续费提醒不会自动扣费或取消订阅。"),
    ],
}


def knowledge_documents(topic: str) -> list[dict[str, str]]:
    selected = topic if topic in TOPICS else "general"
    return [{"id": f"guide.{selected}.{key}", "topic": selected, "text": text,
             "source": f"product://{KNOWLEDGE_VERSION}/{selected}/{key}"} for key, text in DOCUMENTS[selected]]


def capability_catalog() -> list[dict[str, Any]]:
    """One entry per validated executable intent, with no invented tools."""
    return [{"intent": intent, "description": DESCRIPTIONS[intent],
             "mode": "read" if intent in GREEN_INTENTS - {"WEALTH_RISK_ASSESSMENT"} else "dialogue" if intent == "UNKNOWN" else "workflow",
             "baseline": "GREEN" if intent in GREEN_INTENTS else "RED" if intent in RED_INTENTS else "CORE_POLICY",
             "required_fields": missing_slots({"intent": intent})} for intent in sorted(ALL_INTENTS)]
