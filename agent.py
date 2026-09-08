import os
import json
import re
import requests
from typing import TypedDict, Optional, Dict, Any, List
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

if not DEEPSEEK_API_KEY:
    print("⚠️ 警告：未检测到 DEEPSEEK_API_KEY，请在系统环境变量或 .env 中配置！")

llm = ChatOpenAI(
    model="deepseek-chat",  # deepseek-chat 即 DeepSeek-V3/V4 对话模型
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
    temperature=0.0
)

BANK_API_BASE = "http://127.0.0.1:8000/api/v1"

# 1. 状态机全局 State
class BankingAgentState(TypedDict):
    user_id: str
    user_input: str
    intent: Optional[str]
    target: Optional[str]
    amount: Optional[float]
    date_scope: Optional[str]    # 新增：TODAY / MONTH / ALL
    extra_info: Optional[str]
    risk_level: Optional[str]
    auth_token: Optional[str]
    action_requirements: list
    execution_result: Optional[dict[str, Any]]
    chart_data: Optional[list[dict[str, Any]]]
    todo_card: Optional[dict[str, Any]]
    final_response: Optional[str]

def extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(text.strip())

# 2. 节点 1：意图与实体槽位提取（支持跨场景复合意图）
def intent_parser_node(state: BankingAgentState) -> Dict[str, Any]:
    prompt = f"""你是一个银行 AI 核心助理。根据用户指令输出标准 JSON。
严禁输出 Markdown 标记或多余文字。

JSON 格式规范：
{{
  "intent": "BALANCE_QUERY" | "BILL_QUERY" | "BILL_ANALYSIS" | "TRANSFER" | "CROSS_SCENE_LOCK" | "UNKNOWN",
  "target": "转账收款人" 或 null,
  "amount": 浮点数金额,
  "date_scope": "TODAY" | "MONTH" | "ALL",
  "extra_info": 补充说明 或 null
}}

【细分规则】
1. 仅普通查账（如“查看今日账单”、“查查最近流水”） -> intent 为 "BILL_QUERY"。
2. 明确要求深度分析、排查风险（如“为什么上月花销大”、“帮我分析账单”、“有无异常交易”） -> intent 为 "BILL_ANALYSIS"。
3. 时间范围判定：提到“今天/今日” -> date_scope 为 "TODAY"；提到“上月/本月” -> date_scope 为 "MONTH"；未提及具体时间 -> date_scope 为 "ALL"。

用户指令: {state['user_input']}
"""
    try:
        res = llm.invoke([
            SystemMessage(content="You are a banking JSON parser. Only output valid JSON without markdown."),
            HumanMessage(content=prompt)
        ])
        data = extract_json(res.content)
        return {
            "intent": data.get("intent", "UNKNOWN"),
            "target": data.get("target"),
            "amount": float(data.get("amount", 0.0)),
            "date_scope": data.get("date_scope", "ALL"),
            "extra_info": data.get("extra_info"),
            "risk_level": None,
            "auth_token": None,
            "execution_result": None,
            "chart_data": None,
            "todo_card": None,
            "final_response": None
        }
    except Exception as e:
        print(f"[意图识别异常] {e}")
        return {
            "intent": "UNKNOWN", "target": None, "amount": 0.0, "date_scope": "ALL",
            "extra_info": None, "risk_level": None, "auth_token": None,
            "execution_result": None, "chart_data": None, "todo_card": None, "final_response": None
        }

# 3. 节点 2：风控前置评估与分级
def risk_checker_node(state: BankingAgentState) -> Dict[str, Any]:
    intent = state.get("intent")

    # 纯查询、账单分析为绿区
    if intent in ["BALANCE_QUERY", "BILL_QUERY", "BILL_ANALYSIS"]:
        return {"risk_level": "GREEN", "auth_token": None, "action_requirements": []}
    # 跨场景资金锁定：属于低风险用户确认级（黄区）
    if intent == "CROSS_SCENE_LOCK":
        return {
            "risk_level": "YELLOW",
            "auth_token": "token_lock_direct",
            "action_requirements": ["USER_CLICK_CONFIRM"]
        }

    # 转账操作走核心风控预检
    if intent == "TRANSFER":
        target = state.get("target") or "李四"
        amount = state.get("amount", 0.0)
        res = requests.post(f"{BANK_API_BASE}/risk/precheck", json={
            "from_user": state["user_id"],
            "target_name_or_id": target,
            "amount": amount
        })
        if res.status_code == 200:
            data = res.json()
            return {
                "risk_level": data["risk_level"],
                "auth_token": data["auth_token"],
                "action_requirements": data["required_actions"]
            }
        else:
            return {"risk_level": "ERROR", "final_response": f"风控评估未通过: {res.text}"}

    return {"risk_level": "UNKNOWN", "final_response": "未能理解您的银行业务需求，请重新输入。"}

# 4. 节点 3：Human-in-the-loop 挂起与中断
def human_review_node(state: BankingAgentState) -> Dict[str, Any]:
    risk_level = state["risk_level"]

    if risk_level in ["YELLOW", "RED"]:
        desc = (
            f"锁定活期资金【¥{state['amount']}】用于跨场景任务"
            if state["intent"] == "CROSS_SCENE_LOCK"
            else f"向【{state['target']}】转账【¥{state['amount']}】"
        )
        user_confirmed = interrupt({
            "message": f"您正准备进行：{desc}",
            "risk_level": risk_level,
            "intent": state["intent"],
            "target": state["target"],
            "amount": state["amount"],
            "extra_info": state.get("extra_info"),
            "required_actions": state["action_requirements"]
        })
        if not user_confirmed:
            return {"final_response": "操作已由您主动取消，核心银行未做任何资金扣减。"}

    return {}

# 5. 节点 4：核心执行节点
def executor_node(state: BankingAgentState) -> Dict[str, Any]:
    if state.get("final_response"):
        return {}

    intent = state["intent"]
    user_id = state["user_id"]

    # 绿区 1：查余额
    if intent == "BALANCE_QUERY":
        res = requests.get(f"{BANK_API_BASE}/accounts/{user_id}/balance").json()
        return {"execution_result": {"type": "BALANCE", "data": res}}

    # 绿区 2：账单查询或深度分析
    elif intent in ["BILL_QUERY", "BILL_ANALYSIS"]:
        res = requests.get(f"{BANK_API_BASE}/accounts/{user_id}/transactions").json()
        all_records = res.get("data", [])
        
        # 获取今天日期（与流水格式 YYYY-MM-DD 对齐）
        from datetime import datetime
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # 时间过滤
        date_scope = state.get("date_scope", "ALL")
        if date_scope == "TODAY":
            # 仅保留今天的交易（包括刚刚转账生成的新流水）
            filtered = [tx for tx in all_records if tx.get("time", "").startswith(today_str)]
        else:
            filtered = all_records

        return {
            "execution_result": {
                "type": intent,  # 区分是普通 BILL_QUERY 还是深度 BILL_ANALYSIS
                "date_scope": date_scope,
                "records": filtered
            },
            "chart_data": filtered if intent == "BILL_ANALYSIS" else None  # 仅分析模式画图
        }

    # 跨场景联动：锁定资金 + 派发定时任务日程
    elif intent == "CROSS_SCENE_LOCK":
        amount = state.get("amount", 1000.0)
        res = requests.post(f"{BANK_API_BASE}/accounts/lock_fund", json={
            "user_id": user_id,
            "amount": amount,
            "reason": f"跨场景联动储备：{state.get('extra_info', '生日订花/蛋糕')}"
        })
        card_info = {
            "title": "🎂 爱人生日提醒与代订备忘录",
            "locked_fund": f"¥{amount:.2f}",
            "schedule": "生日倒计时 2 天自动预订鲜花与蛋糕",
            "status": "已入库定时任务队列"
        }
        return {
            "execution_result": {"type": "LOCK_SUCCESS", "data": res.json()},
            "todo_card": card_info
        }

    # 转账交易执行
    elif intent == "TRANSFER":
        headers = {"x-auth-token": state.get("auth_token", "")}
        res = requests.post(
            f"{BANK_API_BASE}/transfers/execute",
            headers=headers,
            json={
                "from_user": user_id,
                "target_name_or_id": state["target"],
                "amount": state["amount"]
            }
        )
        if res.status_code == 200:
            return {"execution_result": {"type": "TRANSFER_SUCCESS", "data": res.json()}}
        else:
            return {"final_response": f"转账结算失败: {res.json().get('detail', '核心系统拒绝')}"}

    return {"execution_result": {"type": "UNKNOWN"}}

# 6. 节点 5：智能拟人化应答节点 (Responder)
def responder_node(state: BankingAgentState) -> Dict[str, Any]:
    # 如果前面流程已经有了回复（比如被取消或报错），直接退出
    if state.get("final_response"):
        return {}

    exec_result = state.get("execution_result", {})
    rtype = exec_result.get("type")

    # ================= 场景 1：查余额 =================
    if rtype == "BALANCE":
        d = exec_result["data"]
        return {
            "final_response": (
                f"查询成功：您的账户总余额为 **¥{d['total_balance']:,.2f}**，"
                f"其中锁定/冻结资金 **¥{d['locked_amount']:,.2f}**，"
                f"当前可用余额为 **¥{d['available_balance']:,.2f}**。"
            )
        }

    # ================= 场景 2：普通查账（如“查看今日账单”） =================
    elif rtype == "BILL_QUERY":
        records = exec_result.get("records", [])
        date_scope = exec_result.get("date_scope", "ALL")
        scope_name = "今日" if date_scope == "TODAY" else "近期"

        # 如果没有交易明细
        if not records:
            return {"final_response": f"您好，查询到您**{scope_name}**暂无任何交易支出记录。"}

        # 直接用 Python 拼接账单列表，不让大模型胡乱加戏
        bill_lines = []
        total_spent = 0.0
        for tx in records:
            bill_lines.append(f"- **{tx['time']}** | {tx['merchant']} ({tx['category']}) | **¥{tx['amount']:.2f}**")
            total_spent += float(tx["amount"])

        response_text = (
            f"📋 **为您查询到{scope_name}交易明细（共 {len(records)} 笔）：**\n\n"
            + "\n".join(bill_lines)
            + f"\n\n💰 **{scope_name}累计支出总计：¥{total_spent:,.2f}**"
        )
        return {"final_response": response_text}

    # ================= 场景 3：深度财务分析（明确要求分析/查异常时才触发） =================
    elif rtype == "BILL_ANALYSIS":
        records = exec_result.get("records", [])
        prompt = f"""你是一名专业的银行财务顾问。请根据以下账单明细，客观分析用户的花销结构：
用户提问：{state['user_input']}
账单明细：{json.dumps(records, ensure_ascii=False)}

回复要求：
1. 总结主要开支流向（按分类占比）。
2. 如果存在深夜大额或境外商户等非正常交易，进行安全风险提醒；若无明显异常则如实告知。
3. 排版清晰友好，直接输出 Markdown 文本。
"""
        reply = llm.invoke([HumanMessage(content=prompt)]).content
        return {"final_response": reply}

    # ================= 场景 4：跨场景爱人资金锁定 =================
    elif rtype == "LOCK_SUCCESS":
        d = exec_result["data"]
        return {
            "final_response": (
                f"跨场景任务已成功建立！\n\n"
                f"- **活期资金锁定**：已为您隔离锁定 **¥{state.get('amount', 1000):.2f}** 专款专用。\n"
                f"- **剩余可用余额**：当前可用余额已更新为 **¥{d.get('remaining_available', 0.0):,.2f}**。\n"
                f"- **自动化日程已生成**：已为您排定在生日前 2 天自动推送订购鲜花与蛋糕服务。"
            )
        }

    # ================= 场景 5：转账成功 =================
    elif rtype == "TRANSFER_SUCCESS":
        d = exec_result["data"]
        return {
            "final_response": (
                f"✅ **转账结算成功**\n\n"
                f"- **交易流水号**：`{d['transaction_id']}`\n"
                f"- **收款账户**：{d['receiver_name']}\n"
                f"- **转出金额**：¥{d['transferred_amount']:,.2f}\n"
                f"- **账户当前余额**：¥{d['sender_balance']:,.2f}"
            )
        }

    return {"final_response": "业务已办理完成。"}

# 7. 路由规则与图编排
def route_after_risk_check(state: BankingAgentState) -> str:
    if state["risk_level"] in ["YELLOW", "RED"]:
        return "human_review"
    elif state["risk_level"] == "GREEN":
        return "executor"
    return END

def route_after_human_review(state: BankingAgentState) -> str:
    if state.get("final_response"):
        return END
    return "executor"

workflow = StateGraph(BankingAgentState)

workflow.add_node("intent_parser", intent_parser_node)
workflow.add_node("risk_checker", risk_checker_node)
workflow.add_node("human_review", human_review_node)
workflow.add_node("executor", executor_node)
workflow.add_node("responder", responder_node)

workflow.set_entry_point("intent_parser")
workflow.add_edge("intent_parser", "risk_checker")
workflow.add_conditional_edges("risk_checker", route_after_risk_check)
workflow.add_conditional_edges("human_review", route_after_human_review)
workflow.add_edge("executor", "responder")
workflow.add_edge("responder", END)

memory = MemorySaver()
app = workflow.compile(checkpointer=memory)