from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
import uuid
from datetime import datetime

app = FastAPI(
    title="Mock Bank Core API",
    description="银行 AI 智能体受控沙箱环境",
    version="1.0.0"
)

# 1. 账户数据（内存模拟）
ACCOUNTS = {
    "user_zhang": {
        "card_no": "622202001",
        "name": "张三",
        "phone": "13800138000",
        "balance": 15000.0,
        "locked_amount": 0.0,
        "status": "ACTIVE"
    },
    "user_li": {
        "card_no": "622202002",
        "name": "李四",
        "phone": "13900139000",
        "balance": 2000.0,
        "locked_amount": 0.0,
        "status": "ACTIVE"
    },
    "user_wife": {
        "card_no": "622202003",
        "name": "王美（爱人）",  # 改为专属身份
        "phone": "13700137000",
        "balance": 5000.0,
        "locked_amount": 0.0,
        "status": "ACTIVE"
    }
}

# 2. 别名与联系人映射
CONTACTS = {
    "李四": "user_li",
    "爱人": "user_wife",
    "老婆": "user_wife",
    "王美": "user_wife"
}

# 3. 当日累计转账限额记录（累计 > 1000 元自动升级为红色强验证）
DAILY_TRANSFERS = {"user_zhang": 0.0}

# 4. 历史交易流水
TRANSACTIONS = [
    {"tx_id": "TX1001", "account_id": "user_zhang", "merchant": "美团外卖", "category": "餐饮", "amount": 45.0, "time": "2026-09-01 12:30:00"},
    {"tx_id": "TX1002", "account_id": "user_zhang", "merchant": "Apple 订阅", "category": "订阅代扣", "amount": 15.0, "time": "2026-09-03 08:00:00"},
    {"tx_id": "TX1003", "account_id": "user_zhang", "merchant": "海底捞火锅", "category": "餐饮", "amount": 320.0, "time": "2026-09-05 19:20:00"},
    {"tx_id": "TX1004", "account_id": "user_zhang", "merchant": "境外未知商户", "category": "可疑交易", "amount": 999.0, "time": "2026-09-07 03:15:00"},
]

# 5. 一次性安全凭证池
AUTH_TOKENS = {}

class PrecheckRequest(BaseModel):
    from_user: str
    target_name_or_id: str
    amount: float
    remark: Optional[str] = "智能转账"

class TransferExecuteRequest(BaseModel):
    from_user: str
    target_name_or_id: str
    amount: float
    remark: Optional[str] = "智能转账"

class LockFundRequest(BaseModel):
    user_id: str
    amount: float
    reason: str

@app.get("/api/v1/accounts/{user_id}/balance")
def get_balance(user_id: str):
    """【绿区】查询余额与可用资金"""
    acc = ACCOUNTS.get(user_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账户不存在")
    available = acc["balance"] - acc["locked_amount"]
    return {
        "code": 200,
        "user_name": acc["name"],
        "total_balance": acc["balance"],
        "locked_amount": acc["locked_amount"],
        "available_balance": available,
        "status": acc["status"]
    }

@app.get("/api/v1/accounts/{user_id}/transactions")
def get_transactions(user_id: str):
    """【绿区】消费流水查询"""
    records = [tx for tx in TRANSACTIONS if tx["account_id"] == user_id]
    return {"code": 200, "total_records": len(records), "data": records}

@app.post("/api/v1/risk/precheck")
def risk_precheck(req: PrecheckRequest):
    """【风控前置评估】计算累计金额，定级并下发临时凭证"""
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="转账金额必须大于 0")
    
    current_spent = DAILY_TRANSFERS.get(req.from_user, 0.0)
    accumulated_after = current_spent + req.amount

    # 规则：累计 <= 1000 元为黄区，> 1000 元升级为红区
    if accumulated_after <= 1000.0:
        risk_level = "YELLOW"
        requires = ["USER_CLICK_CONFIRM"]
    else:
        risk_level = "RED"
        requires = ["SMS_OTP", "FACE_RECOGNITION"]

    token = f"auth_token_{uuid.uuid4().hex[:10]}"
    AUTH_TOKENS[token] = {
        "from_user": req.from_user,
        "amount": req.amount,
        "risk_level": risk_level,
        "used": False
    }

    return {
        "risk_level": risk_level,
        "daily_accumulated_now": current_spent,
        "daily_accumulated_after": accumulated_after,
        "auth_token": token,
        "required_actions": requires
    }

@app.post("/api/v1/transfers/execute")
def execute_transfer(req: TransferExecuteRequest, x_auth_token: str = Header(...)):
    """【黄/红区执行】必须携带由前置验证通过的 x_auth_token"""
    auth_record = AUTH_TOKENS.get(x_auth_token)
    if not auth_record or auth_record["used"]:
        raise HTTPException(status_code=403, detail="安全验证已失效或凭证不存在，严禁直接执行")
    
    if auth_record["from_user"] != req.from_user or auth_record["amount"] != req.amount:
        raise HTTPException(status_code=400, detail="转账参数与安全凭证预检信息不一致")

    target_id = CONTACTS.get(req.target_name_or_id, req.target_name_or_id)
    sender = ACCOUNTS.get(req.from_user)
    receiver = ACCOUNTS.get(target_id)

    if not sender or not receiver:
        raise HTTPException(status_code=404, detail="付款方或收款方账户不存在")

    available = sender["balance"] - sender["locked_amount"]
    if available < req.amount:
        raise HTTPException(status_code=400, detail="可用余额不足（部分资金已被锁定）")

    # 扣减记账
    sender["balance"] -= req.amount
    receiver["balance"] += req.amount
    DAILY_TRANSFERS[req.from_user] = DAILY_TRANSFERS.get(req.from_user, 0.0) + req.amount
    auth_record["used"] = True

    txn_id = f"TXN{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}"
    new_record = {
        "tx_id": txn_id,
        "account_id": req.from_user,
        "merchant": f"转账给 {receiver['name']}",
        "category": "转账支出",
        "amount": req.amount,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    TRANSACTIONS.append(new_record)

    return {
        "code": 200,
        "status": "SUCCESS",
        "transaction_id": txn_id,
        "transferred_amount": req.amount,
        "sender_balance": sender["balance"],
        "receiver_name": receiver["name"]
    }

@app.post("/api/v1/accounts/lock_fund")
def lock_fund(req: LockFundRequest):
    """【跨场景联动接口】锁定活期资金"""
    acc = ACCOUNTS.get(req.user_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账户不存在")
    if (acc["balance"] - acc["locked_amount"]) < req.amount:
        raise HTTPException(status_code=400, detail="可用余额不足以锁定此金额")
    
    acc["locked_amount"] += req.amount
    return {
        "code": 200,
        "status": "LOCKED",
        "locked_amount": acc["locked_amount"],
        "remaining_available": acc["balance"] - acc["locked_amount"],
        "reason": req.reason
    }