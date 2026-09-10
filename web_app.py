"""Streamlit channel for the banking agent.

The browser is treated as an untrusted presentation layer.  It may display a
pending approval and collect factors, but the Core API confirms the signed
nonce and MFA proof before any ledger mutation.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Optional

import pandas as pd
import requests
import streamlit as st

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

from agent import app
try:
    from langgraph.types import Command
except Exception:  # pragma: no cover - dependency guidance path
    Command = None
from security import sign_face_attestation


BANK_API_BASE = os.getenv("BANK_API_BASE", "http://127.0.0.1:8000/api/v1").rstrip("/")
DEMO_USER_ID = os.getenv("BANK_DEMO_USER_ID", "user_zhang")
HTTP_TIMEOUT = max(1.0, min(float(os.getenv("BANK_HTTP_TIMEOUT_SECONDS", "8")), 30.0))

st.set_page_config(page_title="AI Banking Agent", page_icon="🏦", layout="wide")


def _headers(*, request_id: Optional[str] = None, token: Optional[str] = None) -> dict[str, str]:
    result = {"X-User-Id": DEMO_USER_ID, "X-Request-Id": request_id or f"web_{uuid.uuid4().hex}"}
    if token:
        result["X-Auth-Token"] = token
    return result


def _get(path: str) -> Optional[dict[str, Any]]:
    try:
        response = requests.get(f"{BANK_API_BASE}/{path.lstrip('/')}", headers=_headers(), timeout=HTTP_TIMEOUT)
        if response.status_code >= 400:
            return None
        value = response.json()
        return value if isinstance(value, dict) else None
    except (requests.RequestException, ValueError):
        return None


def _post(path: str, payload: dict[str, Any], *, token: Optional[str] = None, request_id: Optional[str] = None) -> tuple[bool, Any]:
    try:
        response = requests.post(
            f"{BANK_API_BASE}/{path.lstrip('/')}",
            json=payload,
            headers=_headers(request_id=request_id, token=token),
            timeout=HTTP_TIMEOUT,
        )
        try:
            body = response.json()
        except ValueError:
            body = {"message": response.text}
        return response.status_code < 400, body
    except requests.RequestException as exc:
        return False, {"message": f"核心服务不可用：{exc}"}


def _append_assistant(state: dict[str, Any]) -> None:
    response = state.get("final_response") or "核心服务没有返回可核验结果。"
    st.session_state.messages.append({
        "role": "assistant",
        "content": response,
        "chart_data": state.get("chart_data"),
        "todo_card": state.get("todo_card"),
        "plan": state.get("plan"),
    })


def _resume(decision: Any) -> None:
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    if Command is None or app is None:
        st.error("LangGraph 依赖未安装，无法恢复任务。")
        return
    try:
        list(app.stream(Command(resume=decision), config=config, stream_mode="values"))
        state = app.get_state(config).values
        _append_assistant(state)
        st.session_state.pending_interrupt = None
        st.rerun()
    except Exception as exc:
        st.error(f"恢复任务失败，未确认任何资金变更：{exc}")


def _render_result_extras(msg: dict[str, Any]) -> None:
    chart_data = msg.get("chart_data")
    if chart_data:
        frame = pd.DataFrame(chart_data)
        if not frame.empty and {"category", "amount"}.issubset(frame.columns):
            col_a, col_b = st.columns(2)
            with col_a:
                st.write("📊 **开销类目金额分布**")
                st.bar_chart(frame.groupby("category")["amount"].sum())
            with col_b:
                st.write("📋 **交易流水明细**")
                st.dataframe(frame, use_container_width=True, hide_index=True)
    card = msg.get("todo_card")
    if card:
        st.info(
            f"📌 **{card.get('title', '待办任务')}**\n\n"
            f"- **锁定资产**：{card.get('locked_fund', '—')}\n"
            f"- **执行计划**：{card.get('schedule', '—')}\n"
            f"- **状态**：{card.get('status', '—')}\n"
            f"- **任务编号**：{card.get('task_id', '—')}\n"
            f"- **订单草稿**：{', '.join(card.get('order_task_ids', [])) or '—'}"
        )
    plan = msg.get("plan")
    if plan and plan.get("tasks"):
        with st.expander("查看任务 DAG / 审计摘要"):
            st.dataframe(
                pd.DataFrame([
                    {"task_id": task.get("task_id"), "action": task.get("action"), "depends_on": ",".join(task.get("depends_on", [])), "risk": task.get("risk_level"), "status": task.get("status")}
                    for task in plan["tasks"]
                ]),
                use_container_width=True,
                hide_index=True,
            )


def _render_sidebar() -> None:
    with st.sidebar:
        st.header("💳 核心账户实时看板")
        balance = _get(f"accounts/{DEMO_USER_ID}/balance")
        if balance:
            st.metric("当前可用资金", f"¥ {float(balance.get('available_balance', 0)):,.2f}")
            st.metric("已隔离锁定资金", f"¥ {float(balance.get('locked_amount', 0)):,.2f}")
            st.caption(f"总资产：¥{float(balance.get('total_balance', 0)):,.2f} | 状态：{balance.get('status', '—')}")
        else:
            st.warning("无法连接核心银行沙箱，请先启动 main.py。")
        st.divider()
        st.markdown("**快捷演示指令**")
        st.code("查余额\n给我老婆转账 300 元买菜\n给李四转 2000 元\n分析上月账单有没有异常\n我爱人生日下周，锁定 1000 元并提醒订花\n推荐稳健理财产品\n查看订阅并取消 Apple")
        tasks_payload = _get("tasks")
        if tasks_payload and tasks_payload.get("data"):
            with st.expander("任务中心"):
                for task in tasks_payload["data"]:
                    st.write(f"{task.get('task_id')} · {task.get('task_type')} · {task.get('status')}")
                    if task.get("status") in {"PENDING", "WAITING_USER", "FAILED"}:
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("取消", key=f"cancel_{task.get('task_id')}"):
                                ok, body = _post(f"tasks/{task.get('task_id')}/cancel", {"reason": "用户在任务中心取消"})
                                st.toast("已取消" if ok else f"取消失败：{body}")
                                st.rerun()
                        with c2:
                            if st.button("人工接管", key=f"take_{task.get('task_id')}"):
                                ok, body = _post(f"tasks/{task.get('task_id')}/takeover", {"reason": "用户请求人工接管"})
                                st.toast("已转人工" if ok else f"接管失败：{body}")
                                st.rerun()
        st.caption("所有数据均为虚构沙箱数据；红区验证码仅在本地演示模式有效。")


_render_sidebar()
st.title("🏦 AI 银行智能体")
st.caption("自然语言 → 结构化意图 → 策略分级 → 服务端确认/MFA → 可核验结果")

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "您好！我可以帮您查询余额/账单、转账、分析消费、管理理财、卡片和订阅。"}]
if "thread_id" not in st.session_state:
    st.session_state.thread_id = f"web_session_{uuid.uuid4().hex[:12]}"
if "pending_interrupt" not in st.session_state:
    st.session_state.pending_interrupt = None

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        _render_result_extras(message)


def _render_approval() -> None:
    pending = st.session_state.pending_interrupt
    if not pending:
        return
    risk = pending.get("risk_level", "YELLOW")
    token = pending.get("auth_token")
    request_id = pending.get("request_id")
    with st.chat_message("assistant"):
        if risk == "YELLOW":
            st.warning("⚠️ **黄色风险：请确认本次操作**")
            st.write(pending.get("message", "请确认操作详情。"))
            st.caption("确认会调用核心服务端 /auth/confirm；浏览器按钮本身不具备记账权限。")
            col_ok, col_cancel = st.columns(2)
            with col_ok:
                if st.button("✅ 确认执行", key="approval_yes", use_container_width=True):
                    if not token:
                        st.error("授权凭证缺失，请重新发起操作。")
                    else:
                        ok, body = _post("auth/confirm", {}, token=token, request_id=request_id)
                        if ok:
                            _resume({"confirmed": True})
                        else:
                            st.error(f"核心服务未接受确认：{body}")
            with col_cancel:
                if st.button("❌ 取消操作", key="approval_no", use_container_width=True):
                    _resume({"confirmed": False})
        else:
            # Red operations need two factors verified by the Core API before
            # the graph is resumed.  The demo "device SDK" (this process) signs
            # a server-issued liveness challenge with the attestation key; the
            # OTP and the signed ticket never reach the LLM or session history.
            st.error("🚨 **红色风险：必须完成服务端多因子认证**")
            st.write(pending.get("message", "高风险操作待验证。"))
            face = st.checkbox("在设备端完成模拟活体核验并出具人脸票据", key="mfa_face")
            otp = st.text_input("短信验证码（本地演示码见 .env 配置）", type="password", key="mfa_otp")
            col_ok, col_cancel = st.columns(2)
            with col_ok:
                if st.button("🔐 验证并执行", type="primary", key="mfa_submit", use_container_width=True):
                    if not token:
                        st.error("授权凭证缺失，请重新发起操作。")
                    elif not face:
                        st.error("请先完成设备端活体核验（勾选上方复选框后重新提交）。")
                    else:
                        challenge_ok, challenge_body = _post("auth/mfa/face/challenge", {"auth_token": token}, token=token, request_id=request_id)
                        if not (challenge_ok and isinstance(challenge_body, dict) and challenge_body.get("challenge_id")):
                            st.error(f"无法取得人脸挑战：{challenge_body}")
                        else:
                            attestation = sign_face_attestation(
                                challenge_body["challenge_id"],
                                user_id=DEMO_USER_ID,
                                token_jti=challenge_body["auth_token_jti"],
                            )
                            ok, body = _post("auth/mfa/verify", {"otp": otp, "face_attestation": attestation}, token=token, request_id=request_id)
                            if ok and isinstance(body, dict) and body.get("mfa_proof"):
                                _resume({"confirmed": True, "mfa_proof": body["mfa_proof"]})
                            else:
                                st.error(f"MFA 未通过：{body}")
            with col_cancel:
                if st.button("放弃本次交易", key="mfa_cancel", use_container_width=True):
                    _resume({"confirmed": False})


if app is None or Command is None:
    st.error("未安装 LangGraph 依赖，请先执行 `pip install -r requirements.txt`。")
    st.stop()

_render_approval()

if prompt := st.chat_input("请输入银行业务指令…", disabled=bool(st.session_state.pending_interrupt)):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    with st.chat_message("assistant"):
        with st.spinner("正在进行意图解析与安全预检…"):
            try:
                list(app.stream({"user_id": DEMO_USER_ID, "user_input": prompt}, config=config, stream_mode="values"))
                graph_state = app.get_state(config)
            except Exception as exc:
                st.error(f"请求未执行，核心状态未改变：{exc}")
                st.stop()
        if graph_state.next:
            try:
                interrupt_payload = graph_state.tasks[0].interrupts[0].value
            except (AttributeError, IndexError, KeyError):
                interrupt_payload = {"message": "操作待确认", "risk_level": "YELLOW"}
            st.session_state.pending_interrupt = interrupt_payload
            st.rerun()
        else:
            state = graph_state.values
            st.markdown(state.get("final_response") or "未取得可核验结果。")
            _render_result_extras(state)
            _append_assistant(state)
            st.rerun()
