"""End-to-end probe for real FCM delivery (acceptance for the push channel).

This is the one probe that can only pass with a live Firebase project.  It walks
the *product* path that ends in a notification, and it refuses to call a
configured-but-broken deployment a success:

1. log in for real (the session credential travels on every write);
2. check the deployment actually reports a usable push provider, and that the
   signed-in user has at least one device holding a delivery token - a phone
   with a token and a server with no credential are two different states and
   both must be present for anything below to mean something;
3. run one cross-scene request through the *channel* API (a birthday lock, which
   the agent turns into a red-tier approval);
4. enroll a disposable P-256 probe device and settle with click + SMS + the
   same signed liveness envelope used by Android Keystore;
5. run the scheduler, which moves the resulting order drafts to ``WAITING_USER``
   and - this is the assertion - dispatches a push;
6. require the audit trail to say ``SENT`` with ``delivered >= 1``.  A ``NOT_SENT``
   outcome is a *failure* here, not a soft skip: the point of the probe is that
   the message left the building.

Why a birthday lock rather than a scheduled transfer: the deterministic parser
binds an explicit calendar date to 09:00 Asia/Shanghai, and the core rejects a
plan time that is not in the future - so the soonest a scheduled transfer can
fall due is tomorrow morning.  A birthday *tomorrow* puts its derived reminder
(the order drafts' run time) two days earlier, i.e. already due, which makes the
whole path observable in seconds without weakening the product rules.

What it cannot prove: that the handset rendered the notification.  That is
verified on the device (logcat + the notification shade); this probe proves the
server handed a message to Google and Google accepted it.

Usage::

    $env:BANK_REQUIRE_SESSION = '1'
    uvicorn main:app      --port 8000
    uvicorn agent_api:app --port 8100
    python scripts/fcm_delivery_e2e.py [gateway_url] [core_url]

Requires ``BANK_FCM_CREDENTIALS`` to point at a Firebase service-account key
outside the repository and a signed-in app on a device.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
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
PASSWORD = os.getenv("BANK_DEMO_PASSWORD", "Bank@2026")
OTP = os.getenv("BANK_MFA_TEST_OTP", "888888")
INSTRUCTION = "明天是我生日，帮我锁定 5000 元买鲜花和蛋糕"

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}  {detail}")
        FAILURES.append(label)


def section(title: str) -> None:
    print(f"\n[{title}]")


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def main() -> int:
    print(f"gateway={GATEWAY}  core={CORE}  principal={PRINCIPAL}")

    section("0. login")
    login = requests.post(
        f"{CORE}/api/v1/auth/login",
        json={"user_id": PRINCIPAL, "password": PASSWORD},
        timeout=20,
    )
    check("登录成功", login.status_code == 200, login.text[:200])
    if login.status_code != 200:
        return 1
    session = login.json().get("session_token")
    check("拿到会话凭证", bool(session))
    headers = {"X-Session-Token": str(session), "X-User-Id": PRINCIPAL}
    signer = DeviceSigner()
    signer.enroll(CORE, headers, label="fcm-e2e-probe")

    section("1. preconditions the probe depends on")
    health = requests.get(f"{CORE}/api/v1/security/health", timeout=20).json()
    push = health.get("push") or {}
    print(f"  push={push}")
    check("服务端配置了可用的推送凭据", bool(push.get("configured")), str(push.get("reason")))

    devices = requests.get(f"{CORE}/api/v1/devices", headers=headers, timeout=20).json().get("data") or []
    enrolled = [row for row in devices if row.get("push_enabled")]
    check("至少一台设备已持有投递令牌", bool(enrolled), f"devices={len(devices)}")
    if not push.get("configured") or not enrolled:
        print("\n  前置条件不满足：先在设备上登录 App 完成令牌注册，并配置 BANK_FCM_CREDENTIALS。")
        return 1

    section("2. one cross-scene request through the channel API")
    print(f"  指令: {INSTRUCTION}")
    turn = requests.post(
        f"{GATEWAY}/api/v1/agent/messages",
        json={"text": INSTRUCTION},
        headers=headers,
        timeout=180,
    )
    check("渠道 API 接受指令", turn.status_code == 200, turn.text[:300])
    if turn.status_code != 200:
        return 1
    payload = turn.json()
    approval = payload.get("approval") or {}
    print(f"  status={payload.get('status')}  risk={payload.get('risk_level')}  reply={payload.get('reply')}")
    check("请求被识别为需审批", payload.get("status") == "APPROVAL_REQUIRED", str(payload.get("status")))
    approval_id = approval.get("approval_id")
    check("拿到审批编号", bool(approval_id))
    if not approval_id:
        return 1
    required = list(approval.get("required_actions") or [])
    print(f"  required_actions={required}")

    section("3. satisfy the approval")
    attestation = None
    if "FACE_RECOGNITION" in required:
        challenge = requests.post(
            f"{GATEWAY}/api/v1/agent/approvals/{approval_id}/face-challenge",
            headers=headers,
            timeout=30,
        )
        check("签发了活体挑战", challenge.status_code == 200, challenge.text[:200])
        if challenge.status_code != 200:
            return 1
        challenge_body = challenge.json()
        attestation = signer.sign(
            challenge_body["challenge_id"],
            user_id=PRINCIPAL,
            token_jti=challenge_body["auth_token_jti"],
        )
        check("设备侧完成签名", bool(attestation))

    decision: dict[str, Any] = {"decision": "approve"}
    if "SMS_OTP" in required:
        decision["otp"] = OTP
    if attestation:
        decision["face_attestation"] = attestation
    settled = requests.post(
        f"{GATEWAY}/api/v1/agent/approvals/{approval_id}",
        json=decision,
        headers=headers,
        timeout=180,
    )
    check("审批通过并结算", settled.status_code == 200, settled.text[:400])
    if settled.status_code != 200:
        return 1
    outcome = settled.json()
    print(f"  status={outcome.get('status')}")
    check(
        "操作已结算",
        outcome.get("status") in {"APPROVED", "EXECUTED", "SUCCESS", "COMPLETED"},
        str(outcome.get("status")),
    )

    section("4. the order drafts exist and are already due")
    tasks = requests.get(f"{CORE}/api/v1/tasks", headers=headers, timeout=20).json().get("data") or []
    drafts = [row for row in tasks if row.get("task_type") == "ORDER_DRAFT"]
    check("产生了订单草稿任务", bool(drafts), f"tasks={[row.get('task_type') for row in tasks]}")
    if not drafts:
        return 1
    now = datetime.now(timezone.utc)
    for row in drafts:
        print(f"  {row.get('task_id')}  run_at={row.get('run_at')}  status={row.get('status')}")
    # Only drafts still PENDING can be woken by *this* run: an earlier run of the
    # probe leaves its own drafts sitting in WAITING_USER, and counting those
    # would compare this run's dispatch against last run's task.
    due_drafts = [
        row
        for row in drafts
        if row.get("status") == "PENDING" and (_parse_iso(row.get("run_at")) or now) <= now
    ]
    check("本次产生了可到期的草稿", bool(due_drafts), str([(row.get("task_id"), row.get("status")) for row in drafts]))
    if not due_drafts:
        return 1
    target = due_drafts[0]

    section("5. the scheduler wakes the user")
    due = requests.post(f"{CORE}/api/v1/tasks/run-due", headers=headers, timeout=120)
    check("调度执行成功", due.status_code == 200, due.text[:300])
    if due.status_code != 200:
        return 1
    changed = due.json().get("data") or []
    row = next((item for item in changed if item.get("task_id") == target.get("task_id")), None)
    check("任务进入 WAITING_USER", bool(row) and row.get("status") == "WAITING_USER", str(row))
    # Two order drafts (flowers and cake) fall due together and are keyed by the
    # same run time, so the dispatch that lands last is whichever the store
    # yielded last - not necessarily the one picked above.  Assert membership of
    # the set instead of equality with one arbitrary row.
    waiting_ids = {str(item.get("task_id")) for item in changed if item.get("status") == "WAITING_USER"}
    check("到期的草稿都进入 WAITING_USER", waiting_ids >= {str(item.get("task_id")) for item in due_drafts}, str(waiting_ids))

    section("6. delivery actually happened")
    logs = requests.get(f"{CORE}/api/v1/audit/logs?limit=100", headers=headers, timeout=20).json().get("data") or []
    dispatched = [event for event in logs if event.get("action") == "PUSH_DISPATCHED"]
    check("审计中出现了派发记录", bool(dispatched))
    if not dispatched:
        return 1
    latest = dispatched[-1]
    meta = latest.get("metadata") or {}
    print(f"  outcome={latest.get('outcome')}  decision={latest.get('decision')}")
    print(f"  provider={meta.get('provider')}  devices={meta.get('devices')}  delivered={meta.get('delivered')}  failed={meta.get('failed')}")
    print(f"  title={meta.get('title')}  detail={meta.get('detail')}")
    check(
        "派发结果是 SENT（而不是未配置的 NOT_SENT）",
        latest.get("outcome") == "SENT",
        f"{latest.get('outcome')} / {meta.get('detail')}",
    )
    check("Google 接受了至少一条消息", int(meta.get("delivered") or 0) >= 1, f"delivered={meta.get('delivered')}")
    check("没有设备投递失败", int(meta.get("failed") or 0) == 0, f"failed={meta.get('failed')}")
    check("消息指向本次进入待确认的任务", str((meta.get("data") or {}).get("task_id")) in waiting_ids, str(meta.get("data")))
    check("文案说明还需用户确认", "确认" in str(meta.get("title") or ""), str(meta.get("title")))

    print("\n" + ("全部通过：推送已由服务端真实投递。" if not FAILURES else f"失败 {len(FAILURES)} 项: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
