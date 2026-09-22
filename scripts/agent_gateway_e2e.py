"""End-to-end probe for the channel gateway (P1 acceptance).

Exercises the three authorization tiers through the *channel* API - never
through the core directly - to prove that:

1. a read-only turn completes without any credential;
2. a yellow turn pauses on an opaque ``approval_id`` (the signed nonce is NOT
   handed to the client), and approving it settles the transfer;
3. a red turn additionally requires an SMS code and a device-signed liveness
   attestation, and the resulting MFA proof never reaches the client;
4. the whole decision chain of one turn is still visible through the core's
   ``/security/audit/trace/{request_id}`` - i.e. audit continuity survives the
   extra HTTP hop the channel introduces;
5. negative paths hold: a second message while an approval is pending does not
   silently start a new operation, and a resolved approval cannot be replayed.

Usage::

    # Start the core in the deployment posture the probe asserts against.
    $env:BANK_REQUIRE_SESSION = '1'
    uvicorn main:app      --port 8000
    uvicorn agent_api:app --port 8100
    python scripts/agent_gateway_e2e.py [gateway_url] [core_url]

Demo credentials: the accounts share the password ``Bank@2026`` (override with
``BANK_DEMO_PASSWORD``); the probe logs in for real, so every write below travels
with a session credential rather than an asserted identity header.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # Windows consoles default to a legacy code page; keep the report readable.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - older interpreters
    pass

from scripts.device_signer import DeviceSigner  # noqa: E402

GATEWAY = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8100").rstrip("/")
CORE = (sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000").rstrip("/")
PRINCIPAL = os.getenv("BANK_DEMO_USER_ID", "user_zhang")
OTP = os.getenv("BANK_MFA_TEST_OTP", "888888")

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}  {detail}")
        FAILURES.append(label)


SESSION_TOKENS: dict[str, str] = {}
DEMO_PASSWORD = os.getenv("BANK_DEMO_PASSWORD", "Bank@2026")


def login(principal: str) -> Optional[str]:
    """Authenticate against the core and remember the session credential.

    Every subsequent call presents it, so the probe exercises the same path a
    client takes once a deployment requires sessions for writes.
    """

    response = requests.post(
        f"{CORE}/api/v1/auth/login",
        json={"user_id": principal, "password": DEMO_PASSWORD},
        timeout=10,
    )
    if response.status_code != 200:
        print(f"  · 登录 {principal} 失败: HTTP {response.status_code} {response.text[:160]}")
        return None
    token = response.json().get("session_token")
    if token:
        SESSION_TOKENS[principal] = token
    return token


def headers(principal: str = PRINCIPAL, request_id: Optional[str] = None) -> dict[str, str]:
    value = {"X-User-Id": principal, "Accept": "application/json"}
    session = SESSION_TOKENS.get(principal)
    if session:
        value["X-Session-Token"] = session
    if request_id:
        value["X-Request-Id"] = request_id
    return value


def send(text: str, *, conversation_id: str, message_id: Optional[str] = None) -> dict[str, Any]:
    body: dict[str, Any] = {"text": text, "conversation_id": conversation_id}
    if message_id:
        body["message_id"] = message_id
    response = requests.post(f"{GATEWAY}/api/v1/agent/messages", json=body, headers=headers(), timeout=60)
    response.raise_for_status()
    return response.json()


def approve(approval_id: str, payload: dict[str, Any]) -> requests.Response:
    return requests.post(
        f"{GATEWAY}/api/v1/agent/approvals/{approval_id}",
        json=payload,
        headers=headers(),
        timeout=60,
    )


def core_balance() -> float:
    response = requests.get(f"{CORE}/api/v1/accounts/{PRINCIPAL}/balance", headers=headers(), timeout=10)
    response.raise_for_status()
    return float(response.json()["available_balance"])


def trace_actions(request_id: Optional[str]) -> list[str]:
    """Return the core audit actions recorded for one request id."""

    if not request_id:
        return []
    response = requests.get(f"{CORE}/api/v1/security/audit/trace/{request_id}", headers=headers(), timeout=10)
    if response.status_code != 200:
        print(f"  · trace 查询失败: HTTP {response.status_code} {response.text[:160]}")
        return []
    payload = response.json()
    events = payload.get("events") or payload.get("data") or []
    return [str(item.get("action")) for item in events if isinstance(item, dict)]


def main() -> int:
    print(f"gateway = {GATEWAY}")
    print(f"core    = {CORE}")

    print("\n[0] 登录、严格会话模式与网关健康检查")
    check("口令登录成功", login(PRINCIPAL) is not None, "核心拒绝了演示口令")
    check("第二个用户也能登录", login("user_li") is not None, "")

    # Start the core with BANK_REQUIRE_SESSION=1 for this assertion to be
    # meaningful; it is the deployment posture docker-compose ships with.
    bare = requests.post(
        f"{CORE}/api/v1/risk/precheck",
        json={"from_user": PRINCIPAL, "target_name_or_id": "李四", "amount": 10},
        headers={"X-User-Id": PRINCIPAL},
        timeout=10,
    )
    check("严格会话模式：裸身份头写入被拒(401)", bare.status_code == 401, f"HTTP {bare.status_code} {bare.text[:160]}")
    if bare.status_code != 401:
        print("    （提示：核心需以 BANK_REQUIRE_SESSION=1 启动）")

    health = requests.get(f"{GATEWAY}/healthz", timeout=10).json()
    conversation_health = health.get("conversation") or {}
    check("healthz 报告 checkpointer 后端", "checkpointer" in conversation_health, str(health))
    check("checkpointer 为持久化(sqlite)", conversation_health.get("checkpointer") == "sqlite", str(conversation_health.get("checkpointer")))

    start_balance = core_balance()
    print(f"  起始可用余额: {start_balance}")

    # -- 1. green ---------------------------------------------------------
    print("\n[1] 绿区：只读查询")
    green = send("查一下余额", conversation_id=f"c-{uuid.uuid4().hex[:8]}")
    check("状态为 COMPLETED", green.get("status") == "COMPLETED", str(green.get("status")))
    check("未产生审批句柄", green.get("approval") is None)
    check("回复包含余额信息", "余额" in (green.get("reply") or "") or "¥" in (green.get("reply") or ""), green.get("reply"))

    # -- 2. yellow --------------------------------------------------------
    print("\n[2] 黄区：转账 300 元（需点击确认）")
    conv_yellow = f"c-{uuid.uuid4().hex[:8]}"
    yellow = send("给李四转 300 元", conversation_id=conv_yellow)
    check("状态为 APPROVAL_REQUIRED", yellow.get("status") == "APPROVAL_REQUIRED", str(yellow.get("status")))
    check("风险级别为 YELLOW", yellow.get("risk_level") == "YELLOW", str(yellow.get("risk_level")))
    approval = yellow.get("approval") or {}
    approval_id = approval.get("approval_id")
    check("返回了审批句柄", bool(approval_id), str(approval))
    check("响应不含 auth_token（凭证留在服务端）", "auth_token" not in str(yellow), "响应里出现了 auth_token")

    print("  · 审批待定时再发一条消息，不得悄悄开始新操作")
    while_pending = send("再给王美转 500 元", conversation_id=conv_yellow)
    check("仍返回同一个待审批句柄", (while_pending.get("approval") or {}).get("approval_id") == approval_id, str(while_pending))

    resolved = approve(approval_id, {"decision": "approve"})
    check("审批返回 200", resolved.status_code == 200, f"HTTP {resolved.status_code} {resolved.text[:200]}")
    body = resolved.json()
    check("审批后状态为 COMPLETED", body.get("status") == "COMPLETED", str(body.get("status")))
    after_yellow = core_balance()
    check("余额减少 300", abs((start_balance - after_yellow) - 300.0) < 0.01, f"{start_balance} -> {after_yellow}")

    print("  · 已处理的审批不得重放")
    replay = approve(approval_id, {"decision": "approve"})
    check("重放被拒绝", replay.status_code >= 400, f"HTTP {replay.status_code}")

    # -- 3. red -----------------------------------------------------------
    print("\n[3] 红区：转账 2000 元（需 OTP + 设备签名的活体票据）")
    conv_red = f"c-{uuid.uuid4().hex[:8]}"
    red = send("给李四转 2000 元", conversation_id=conv_red)
    check("状态为 APPROVAL_REQUIRED", red.get("status") == "APPROVAL_REQUIRED", str(red.get("status")))
    check("风险级别为 RED", red.get("risk_level") == "RED", str(red.get("risk_level")))
    red_approval = red.get("approval") or {}
    red_id = red_approval.get("approval_id")
    check("要求 MFA", red_approval.get("mfa_required") is True, str(red_approval))

    print("  · 缺少因子的审批必须被拒")
    no_factors = approve(red_id, {"decision": "approve"})
    check("无因子被拒(400)", no_factors.status_code == 400, f"HTTP {no_factors.status_code} {no_factors.text[:160]}")

    print("  · 领取人脸挑战并由“设备”签名")
    signer = DeviceSigner()
    signer.enroll(CORE, headers(), label="gateway-e2e-probe")
    challenge = requests.post(
        f"{GATEWAY}/api/v1/agent/approvals/{red_id}/face-challenge",
        json={},
        headers=headers(),
        timeout=30,
    )
    check("挑战获取成功", challenge.status_code == 200, f"HTTP {challenge.status_code} {challenge.text[:200]}")
    challenge_body = challenge.json()
    check("挑战不含 auth_token", "auth_token" not in str(challenge_body).replace("auth_token_jti", ""), str(challenge_body))
    attestation = signer.sign(
        challenge_body["challenge_id"],
        user_id=PRINCIPAL,
        token_jti=challenge_body["auth_token_jti"],
    )
    red_resolved = approve(red_id, {"decision": "approve", "otp": OTP, "face_attestation": attestation})
    check("红区审批成功", red_resolved.status_code == 200, f"HTTP {red_resolved.status_code} {red_resolved.text[:300]}")
    red_body = red_resolved.json()
    check("状态为 COMPLETED", red_body.get("status") == "COMPLETED", str(red_body.get("status")))
    check("响应不含 mfa_proof", "mfa_proof" not in str(red_body), "mfa_proof 泄露到了客户端")
    after_red = core_balance()
    check("余额再减少 2000", abs((after_yellow - after_red) - 2000.0) < 0.01, f"{after_yellow} -> {after_red}")

    # -- 4. audit continuity ---------------------------------------------
    # One user intent must stay ONE decision chain even though the channel
    # splits it across several HTTP requests (message -> challenge -> approve).
    #
    # The two tiers produce different chains, by design:
    #   yellow: RISK_PRECHECK -> USER_CONFIRMATION -> TRANSFER_EXECUTE
    #   red:    RISK_PRECHECK -> FACE_CHALLENGE_ISSUED -> MFA_VERIFY -> ...
    # A successful MFA already sets the server-side confirmation bit, so the
    # red path deliberately emits no separate USER_CONFIRMATION event.
    print("\n[4] 审计连续性：一次会话的决策链是否可还原")

    red_chain = trace_actions(red_body.get("request_id"))
    check("红区响应带回了 request_id", bool(red_body.get("request_id")), str(red_body.get("request_id")))
    print(f"  · 红区决策链: {red_chain}")
    for required in ("RISK_PRECHECK", "FACE_CHALLENGE_ISSUED", "MFA_VERIFY", "TRANSFER_EXECUTE"):
        check(f"红区链路包含 {required}", required in red_chain, str(red_chain))

    yellow_chain = trace_actions(body.get("request_id"))
    print(f"  · 黄区决策链: {yellow_chain}")
    for required in ("RISK_PRECHECK", "USER_CONFIRMATION", "TRANSFER_EXECUTE"):
        check(f"黄区链路包含 {required}", required in yellow_chain, str(yellow_chain))
    check("红区链路不含 USER_CONFIRMATION（MFA 已内含确认）", "USER_CONFIRMATION" not in red_chain, str(red_chain))

    # -- 5. client isolation ---------------------------------------------
    print("\n[5] 越权：审批句柄与主体绑定")
    # ``user_li`` must pick a contact that is not himself, otherwise the core
    # refuses the precheck and no approval is created at all.
    other = requests.post(
        f"{GATEWAY}/api/v1/agent/messages",
        json={"text": "给王美转 150 元", "conversation_id": f"c-{uuid.uuid4().hex[:8]}"},
        headers=headers("user_li"),
        timeout=60,
    )
    check("另一个主体可以发起自己的会话", other.status_code == 200, f"HTTP {other.status_code} {other.text[:200]}")
    other_body = other.json() if other.status_code == 200 else {}
    other_approval = (other_body.get("approval") or {}).get("approval_id")
    check("该主体拿到了自己的审批句柄", bool(other_approval), str(other_body)[:240])
    if other_approval:
        stolen = requests.post(
            f"{GATEWAY}/api/v1/agent/approvals/{other_approval}",
            json={"decision": "approve"},
            headers=headers(),  # user_zhang 拿着 user_li 的句柄
            timeout=30,
        )
        check("跨主体使用句柄被拒(403)", stolen.status_code == 403, f"HTTP {stolen.status_code} {stolen.text[:160]}")

    # -- 6. streamed turn (SSE) -------------------------------------------
    #
    # Read incrementally with stream=True: buffering the whole body first would
    # hide whether the server actually flushes progress, which is the entire
    # point of the endpoint.
    print("\n[6] 消息流（SSE）：阶段进度与结构化结果")
    stream_conversation = f"c-{uuid.uuid4().hex[:8]}"
    frames: list[tuple[str, Any]] = []
    with requests.post(
        f"{GATEWAY}/api/v1/agent/messages/stream",
        json={"text": "查一下余额", "conversation_id": stream_conversation},
        headers=headers(),
        stream=True,
        timeout=60,
    ) as response:
        check("流式响应状态 200", response.status_code == 200, f"HTTP {response.status_code} {response.text[:160]}")
        content_type = response.headers.get("content-type", "")
        check("Content-Type 为 text/event-stream", content_type.startswith("text/event-stream"), content_type)
        event_name: Optional[str] = None
        buffer = ""
        for raw in response.iter_lines(decode_unicode=True):
            line = raw or ""
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                buffer += line[len("data:"):].strip()
            elif not line.strip() and event_name:
                frames.append((event_name, json.loads(buffer) if buffer else None))
                event_name, buffer = None, ""

    events = [name for name, _ in frames]
    stages = [payload["stage"] for name, payload in frames if name == "stage" and payload]
    print(f"  · 事件序列: {events}")
    check("收到阶段进度事件", len(stages) >= 3, str(stages))
    check("阶段顺序符合图结构", stages[:2] == ["intent_parser", "risk_checker"], str(stages))
    check("终态事件唯一", sum(1 for name in events if name in {"result", "approval", "error"}) == 1, str(events))
    check("终态事件位于末尾", events[-1] == "result", str(events[-1]))

    if events and events[-1] == "result":
        streamed = frames[-1][1]
        check("流式结果状态为 COMPLETED", streamed.get("status") == "COMPLETED", str(streamed.get("status")))
        execution = ((streamed.get("result") or {}).get("execution_result") or {})
        balance = (execution.get("data") or {}).get("available_balance")
        # The structured half is what the app's result view renders; prose alone
        # would leave it nothing to show.
        check("结果携带结构化数据", isinstance(balance, (int, float)), str(execution)[:200])

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"结果: {len(FAILURES)} 项失败")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("结果: 全部通过")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
