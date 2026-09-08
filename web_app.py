import streamlit as st
import uuid
import time
import requests
import pandas as pd
from agent import app
from langgraph.types import Command

BANK_API_BASE = "http://127.0.0.1:8000/api/v1"

st.set_page_config(page_title="AI Banking Agent", page_icon="🏦", layout="wide")

# 侧边栏：实时监控核心银行
with st.sidebar:
    st.header("💳 核心账户实时看板")
    try:
        acc_res = requests.get(f"{BANK_API_BASE}/accounts/user_zhang/balance").json()
        st.metric(label="当前可用资金 (张三)", value=f"¥ {acc_res['available_balance']:,.2f}")
        st.metric(label="已隔离锁定资金", value=f"¥ {acc_res['locked_amount']:,.2f}")
        st.caption(f"账户总资产: ¥{acc_res['total_balance']:,.2f} | 状态: {acc_res['status']}")
    except Exception:
        st.error("无法连接核心银行沙箱 (main.py)，请确保 8000 端口服务正在运行。")
    
    st.write("---")
    st.markdown("🎯 **赛题必测场景快捷提示：**")
    st.markdown("1. **智能转账 (黄区)**：`给我老婆转账 300 块买菜`")
    st.markdown("2. **大额强阻断 (红区)**：`给李四转 2000 元`")
    st.markdown("3. **账单分析与异常检测**：`为什么我上个月花销这么大，有异常交易吗？`")
    st.markdown("4. **跨场景生日联动**：`我爱人生日是下周，帮我锁定 1000 元活期，生日前 2 天提醒我订花和蛋糕`")

st.title("🏦 AI 银行智能体 (AI Banking Agent)")
st.caption("基于 LangGraph 状态机 + 分级风控网关 + 跨场景事件驱动架构")

# 会话状态维护
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "您好！我是您的专属 AI 银行数字管家。支持智能转账、账单分析排查与跨场景资产托管服务。"}
    ]

if "thread_id" not in st.session_state:
    st.session_state.thread_id = f"web_session_{uuid.uuid4().hex[:8]}"

if "pending_interrupt" not in st.session_state:
    st.session_state.pending_interrupt = None

config = {"configurable": {"thread_id": st.session_state.thread_id}}

# 渲染历史对话与附加组件
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        # 若历史消息带图表，则一并恢复渲染
        if "chart_data" in msg and msg["chart_data"]:
            df = pd.DataFrame(msg["chart_data"])
            col_a, col_b = st.columns(2)
            with col_a:
                st.write("📊 **开销类目金额分布**")
                cat_sum = df.groupby("category")["amount"].sum()
                st.bar_chart(cat_sum)
            with col_b:
                st.write("📋 **交易流水明细")
                st.dataframe(df[["time", "merchant", "category", "amount"]], use_container_width=True)

        # 若带日程代办卡片
        if "todo_card" in msg and msg["todo_card"]:
            card = msg["todo_card"]
            st.info(f"📌 **\n\n- **锁定资产**：{card['locked_fund']}\n- **执行计划**：{card['schedule']}\n- **状态**：{card['status']}")

# 处理挂起中断
if st.session_state.pending_interrupt:
    interrupt_info = st.session_state.pending_interrupt
    risk_level = interrupt_info.get("risk_level", "YELLOW")
    intent = interrupt_info.get("intent")
    target = interrupt_info.get("target", "对方")
    amount = interrupt_info.get("amount", 0.0)

    # 1. 黄色风险确认卡片（小额转账或跨场景资金锁定）
    if risk_level == "YELLOW":
        with st.chat_message("assistant"):
            if intent == "CROSS_SCENE_LOCK":
                st.warning("⚠️ **【黄色风险】跨场景专款预留确认**")
                st.write(f"您正准备将活期资金 **¥{amount:.2f}** 移入隔离锁定池，并创建后续代订代办任务。是否确认？")
            else:
                st.warning("⚠️ **【黄色风险】小额敏感操作确认**")
                st.write(f"您正准备向 **{target}** 转账 **¥{amount:.2f}**，是否确认立即出款？")

            col1, col2 = st.columns(2)
            with col1:
                if st.button("✅ 确认执行", use_container_width=True, key="confirm_btn"):
                    with st.spinner("核心银行记账处理中..."):
                        _ = list(app.stream(Command(resume=True), config=config, stream_mode="values"))
                        final_state = app.get_state(config).values
                        resp = final_state.get("final_response", "操作完成。")
                        
                        msg_item = {
                            "role": "assistant",
                            "content": resp,
                            "chart_data": final_state.get("chart_data"),
                            "todo_card": final_state.get("todo_card")
                        }
                        st.session_state.messages.append(msg_item)
                        st.session_state.pending_interrupt = None
                        st.rerun()

            with col2:
                if st.button("❌ 取消操作", use_container_width=True, key="cancel_btn"):
                    _ = list(app.stream(Command(resume=False), config=config, stream_mode="values"))
                    final_state = app.get_state(config).values
                    resp = final_state.get("final_response", "操作已取消。")
                    st.session_state.messages.append({"role": "assistant", "content": resp})
                    st.session_state.pending_interrupt = None
                    st.rerun()

    # 2. 红色高危强验证弹窗（超额转账拦截）
    elif risk_level == "RED":
        @st.dialog("🚨 红色级别高危风控强验证")
        def red_verification_modal():
            st.error("检测到累计转账超 1000 元，触发大额资金强安全阻断！")
            st.markdown(f"**转账目标**：{target}  \n**交易金额**：¥{amount:.2f}")
            st.write("---")

            st.subheader("1. 活体人脸比对")
            face_verified = st.checkbox("【模拟】前置人脸比对通过（活体核验一致）")

            st.subheader("2. 动态短信验证码")
            sms_code = st.text_input("请输入验证码", placeholder="预置测试码: 888888")

            col_sub1, col_sub2 = st.columns(2)
            with col_sub1:
                if st.button("验证并完成支付", type="primary", use_container_width=True):
                    if not face_verified:
                        st.error("请先勾选完成人脸活体核验！")
                    elif sms_code != "888888":
                        st.error("验证码输入有误，请输入 888888！")
                    else:
                        st.success("多因子鉴权通过，核心系统正在清算记账...")
                        time.sleep(0.8)
                        _ = list(app.stream(Command(resume=True), config=config, stream_mode="values"))
                        final_state = app.get_state(config).values
                        resp = final_state.get("final_response", "转账成功。")
                        
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": resp,
                            "chart_data": final_state.get("chart_data"),
                            "todo_card": final_state.get("todo_card")
                        })
                        st.session_state.pending_interrupt = None
                        st.rerun()

            with col_sub2:
                if st.button("放弃本次交易", use_container_width=True):
                    _ = list(app.stream(Command(resume=False), config=config, stream_mode="values"))
                    final_state = app.get_state(config).values
                    resp = final_state.get("final_response", "大额交易已放弃。")
                    st.session_state.messages.append({"role": "assistant", "content": resp})
                    st.session_state.pending_interrupt = None
                    st.rerun()

        red_verification_modal()

# 用户指令输入区
if prompt := st.chat_input("请输入银行业务指令...", disabled=bool(st.session_state.pending_interrupt)):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("AI 深度分析与风控试算中..."):
            _ = list(app.stream(
                {"user_id": "user_zhang", "user_input": prompt},
                config=config,
                stream_mode="values"
            ))

        graph_state = app.get_state(config)
        if graph_state.next:
            interrupted_payload = graph_state.tasks[0].interrupts[0].value
            st.session_state.pending_interrupt = interrupted_payload
            st.rerun()
        else:
            final_resp = graph_state.values.get("final_response", "未能处理该请求。")
            chart_data = graph_state.values.get("chart_data")
            todo_card = graph_state.values.get("todo_card")

            st.markdown(final_resp)
            
            # 若有账单数据，即刻画图
            if chart_data:
                df = pd.DataFrame(chart_data)
                col_a, col_b = st.columns(2)
                with col_a:
                    st.write("📊 **开销类目金额分布**")
                    cat_sum = df.groupby("category")["amount"].sum()
                    st.bar_chart(cat_sum)
                with col_b:
                    st.write("📋 **交易流水明细**")
                    st.dataframe(df[["time", "merchant", "category", "amount"]], use_container_width=True)

            # 若有待办卡片，即刻展示
            if todo_card:
                st.info(f"📌 **{todo_card['title']}**\n\n- **锁定资产**：{todo_card['locked_fund']}\n- **执行计划**：{todo_card['schedule']}\n- **状态**：{todo_card['status']}")

            st.session_state.messages.append({
                "role": "assistant",
                "content": final_resp,
                "chart_data": chart_data,
                "todo_card": todo_card
            })
            st.rerun()