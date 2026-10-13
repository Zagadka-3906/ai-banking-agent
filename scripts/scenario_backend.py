"""Isolated local backend for Android natural-language acceptance tests.

Never import this runner into production. Its control endpoints exist only in
this process, listen on loopback, and require a per-run control token. The normal
8000/8100 stack and its stores are never touched.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=["core", "gateway"])
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--source-dir")
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    args = parser.parse_args()
    folder = Path(args.run_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    if args.source_dir:
        sys.path.insert(0, str(Path(args.source_dir).resolve()))
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    token = os.environ.get("BANK_SCENARIO_CONTROL_TOKEN")
    if not token:
        raise SystemExit("BANK_SCENARIO_CONTROL_TOKEN must be set for this isolated runner")
    os.environ.update({
        "BANK_API_BASE": "http://127.0.0.1:18800/api/v1",
        "BANK_REQUIRE_SESSION": "1", "BANK_DEMO_MODE": "1",
        "BANK_CONVERSATION_DB": "memory", "BANK_APPROVAL_STORE_PATH": "memory",
        "BANK_LEDGER_STORE_PATH": "",
        "BANK_DEVICE_STORE_PATH": str(folder / "devices.json"),
        "BANK_AUDIT_LOG_PATH": str(folder / f"{args.service}-audit.jsonl"),
        "BANK_AGENT_RATE_LIMIT_PER_MINUTE": "0",
        "BANK_RATE_LIMIT_PER_MINUTE": "0",
        "BANK_SESSION_TTL_SECONDS": "86400",
    })
    if args.mode == "offline":
        os.environ["DEEPSEEK_API_KEY"] = ""
    module = importlib.import_module("main" if args.service == "core" else "agent_api")
    app = module.app
    source_root = Path(args.source_dir).resolve() if args.source_dir else ROOT
    fingerprints = {
        name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
        for name in ("agent.py", "agent_api.py", "conversation.py", "main.py", "planner.py", "security.py", "bank_tools.py", "sandbox.py", "intent_language.py", "workflow_contract.py", "workflow_runtime.py")
    }
    (folder / f"{args.service}-run.json").write_text(json.dumps({
        "service": args.service, "mode": args.mode, "source_root": str(source_root),
        "started_at": datetime.now().astimezone().isoformat(), "source_sha256": fingerprints,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.service == "core":
        # Only this runner knows the reset route; it authenticates separately
        # with the isolated control token, before any demo session exists.
        module._SESSION_BOOTSTRAP_PATHS = {*module._SESSION_BOOTSTRAP_PATHS, "/__scenario/reset"}
    from fastapi import Header, HTTPException
    records: list[dict] = []
    model_calls: list[dict] = []

    def authorize(value: str) -> None:
        import hmac
        if not hmac.compare_digest(value, token):
            raise HTTPException(403, "Scenario control token required")

    if args.service == "gateway":
        import agent
        import conversation
        original_stream = conversation.SERVICE.start_turn_stream

        def observe(envelope, text, event, started, principal):
            record = {"input": text, "event": event, "actual": envelope,
                      "elapsed_ms": round((time.monotonic() - started) * 1000)}
            if envelope.get("conversation_id"):
                thread_id = conversation.conversation_thread_id(principal, envelope["conversation_id"])
                state = conversation.get_graph().get_state({"configurable": {"thread_id": thread_id}}).values
                record["parsed"] = {key: state.get(key) for key in (
                    "intent", "target", "amount", "date_scope", "date_range", "parse_meta", "slots", "participants", "product_ids",
                    "scheduled_at", "event_at", "clarification_required", "risk_level",
                )}
            records.append(record)
            with (folder / "turns.jsonl").open("a", encoding="utf-8") as output:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")

        def recording_stream(**kwargs):
            started = time.monotonic()
            for event in original_stream(**kwargs):
                if event.get("event") in {"result", "approval", "error"}:
                    observe(event["data"], kwargs.get("text"), event["event"], started, kwargs["principal"])
                yield event
        conversation.SERVICE.start_turn_stream = recording_stream
        original_approval = conversation.SERVICE.resolve_approval
        def recording_approval(**kwargs):
            started = time.monotonic()
            envelope = original_approval(**kwargs)
            observe(envelope, "[" + kwargs["decision"] + "]", "approval_action", started, kwargs["principal"])
            return envelope
        conversation.SERVICE.resolve_approval = recording_approval
        class ObserveHttpRejections:
            """Record actual failed HTTP responses without changing their body."""
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                path = scope.get("path", "")
                watched = scope.get("type") == "http" and scope.get("method") == "POST" and (
                    path in {"/api/v1/agent/messages", "/api/v1/agent/messages/stream"}
                    or path.startswith("/api/v1/agent/approvals/")
                )
                if not watched:
                    return await self.app(scope, receive, send)
                request_body, response_body = bytearray(), bytearray()
                status = 0
                started = time.monotonic()

                async def observe_receive():
                    message = await receive()
                    if message["type"] == "http.request":
                        request_body.extend(message.get("body", b""))
                    return message

                async def observe_send(message):
                    nonlocal status
                    if message["type"] == "http.response.start":
                        status = message["status"]
                    elif message["type"] == "http.response.body" and status >= 400:
                        response_body.extend(message.get("body", b""))
                    await send(message)

                await self.app(scope, observe_receive, observe_send)
                if status >= 400:
                    try:
                        request = json.loads(request_body or b"{}")
                        response = json.loads(response_body)
                    except (ValueError, UnicodeError):
                        return
                    # Never record the request's OTP, credential or attestation.
                    record = {
                        "event": "http_error", "http_status": status, "response": response,
                        "input": request.get("text") or ("[challenge]" if path.endswith(("/challenge", "/face-challenge")) else f"[{request.get('decision')}]"),
                        "path": path,
                        "conversation_id": request.get("conversation_id"),
                        "observation_index": len(records) + 1,
                        "elapsed_ms": round((time.monotonic() - started) * 1000),
                    }
                    records.append(record)
                    with (folder / "turns.jsonl").open("a", encoding="utf-8") as output:
                        output.write(json.dumps(record, ensure_ascii=False) + "\n")

        app.add_middleware(ObserveHttpRejections)
        if agent.llm is not None:
            real_llm = agent.llm
            class ObservedModel:
                def invoke(self, *positional, **keywords):
                    started = time.monotonic()
                    record = {"success": False}
                    try:
                        value = real_llm.invoke(*positional, **keywords)
                        record["success"] = True
                        return value
                    except Exception as error:
                        record["error_type"] = type(error).__name__
                        raise
                    finally:
                        record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
                        model_calls.append(record)
            agent.llm = ObservedModel()

    @app.post("/__scenario/reset")
    def reset(payload: dict, x_scenario_token: str = Header(default="")):
        authorize(x_scenario_token)
        records.clear()
        model_calls.clear()
        if args.service == "core":
            module.reset_state()
            now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
            this_month = now.replace(day=1)
            previous_month = this_month - timedelta(days=1)
            rows = [
                ("FOOD1", "午餐", "餐饮", 120, this_month, "CARD-001"),
                ("FOOD2", "晚餐", "餐饮", 80, this_month + timedelta(days=1), "CARD-001"),
                ("SHOP", "生活用品", "购物", 300, this_month + timedelta(days=2), "CARD-001"),
                ("TRAVEL", "公共交通", "交通", 40, this_month + timedelta(days=3), "CARD-001"),
                ("SUB", "Apple 订阅", "订阅", 15, this_month + timedelta(days=4), "CARD-001"),
                ("FOREIGN", "境外未知商户", "境外消费", 2000, this_month + timedelta(days=5), "CARD-001"),
                ("LASTFOOD", "上月午餐", "餐饮", 40, previous_month, "CARD-001"),
                ("LASTTRAVEL", "上月交通", "交通", 60, previous_month, "CARD-001"),
                ("LASTYEAR", "去年购物", "购物", 900, now.replace(year=now.year - 1, month=12, day=15), "CARD-001"),
            ]
            module.TRANSACTIONS[:] = [
                {"tx_id": f"TX-SCENARIO-{key}", "account_id": "user_zhang", "merchant": merchant,
                 "category": category, "amount": amount, "time": date.strftime("%Y-%m-%d %H:%M:%S"), "card_id": card}
                for key, merchant, category, amount, date, card in rows
            ]
            for index, row in enumerate(module.SUBSCRIPTIONS["user_zhang"]):
                row["next_charge"] = (now + timedelta(days=7 * (index + 1))).strftime("%Y-%m-%d")
            module.USER_CONTACTS["user_zhang"] = [{
                "contact_id": "CTT-SCENARIO-LI", "owner_id": "user_zhang", "name": "老李",
                "phone": "13900139000", "relationship": "COLLEAGUE", "birthday": None,
                "account_id": "user_li", "linked_existing": True, "link_verification": "PHONE_MATCH",
                "created_at": now.isoformat(),
            }]
            if payload.get("two_cards"):
                import copy
                other = copy.deepcopy(module.CARDS["user_zhang"][0])
                other.update({"card_id": "CARD-002", "number_masked": "**** **** **** 1111", "last4": "1111", "label": "日常消费卡"})
                module.CARDS["user_zhang"].append(other)
            if payload.get("risk_profile"):
                module.WEALTH_RISK_PROFILES["user_zhang"] = {
                    "risk_profile": "C3", "risk_type": "稳健型", "assessed_at": now.isoformat(),
                    "loss_tolerance": "MEDIUM", "horizon_years": 3, "experience": "BASIC",
                }
        else:
            import conversation
            import security
            conversation.SERVICE.reset()
            security.reset_security_state()
            conversation._GRAPH = None
            conversation._GRAPH_BACKEND = "uninitialised"
            module.RATE_LIMIT_STATE.clear()
        return {"ok": True, "mode": args.mode}

    @app.get("/__scenario/records")
    def read_records(x_scenario_token: str = Header(default="")):
        authorize(x_scenario_token)
        return {"records": records, "model_calls": model_calls, "mode": args.mode}

    @app.get("/__scenario/audit")
    def audit(x_scenario_token: str = Header(default="")):
        authorize(x_scenario_token)
        import security
        return {"source": args.service, "events": security.get_audit_logs(),
                "chain": security.verify_audit_chain(),
                "circuit": security.circuit_status("user_zhang"),
                "behavior": security.behavior_status("user_zhang")}

    @app.get("/__scenario/state")
    def state(x_scenario_token: str = Header(default="")):
        authorize(x_scenario_token)
        if args.service != "core":
            return {"mode": args.mode}
        return {
            "account_balances": {key: {field: row.get(field) for field in ("balance", "locked_amount")}
                                 for key, row in module.ACCOUNTS.items()},
            "balance": module.ACCOUNTS["user_zhang"]["balance"],
            "locked_amount": module.ACCOUNTS["user_zhang"]["locked_amount"],
            "transactions": module.TRANSACTIONS,
            "subscriptions": module.SUBSCRIPTIONS["user_zhang"],
            "cards": module.CARDS["user_zhang"],
            "scheduled_transfers": module.SCHEDULED_TRANSFERS,
            "splits": module.SPLIT_REQUESTS,
            "holdings": module.WEALTH_HOLDINGS.get("user_zhang", []),
            "tasks": [task.to_dict() for task in module.TASK_STORE.list(owner_id="user_zhang")],
            "disputes": module.DISPUTE_CASES,
        }

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=18800 if args.service == "core" else 18810)


if __name__ == "__main__":
    run()
