"""Isolated local backend for Android natural-language acceptance tests.

Never import this runner into production. Its control endpoints exist only in
this process, listen on loopback, and require a per-run control token. The normal
8000/8100 stack and its stores are never touched.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ObservedScenarioModel:
    """Record scenario timing without dropping the SDK's structured binding."""

    def __init__(self, model, records):
        self.model, self.records = model, records

    def bind(self, **kwargs):
        return ObservedScenarioModel(self.model.bind(**kwargs), self.records)

    def invoke(self, *args, **kwargs):
        started = time.monotonic()
        record = {'success': False}
        try:
            value = self.model.invoke(*args, **kwargs)
            record['success'] = True
            return value
        except Exception as error:
            record['error_type'] = type(error).__name__
            raise
        finally:
            record['elapsed_ms'] = round((time.monotonic() - started) * 1000)
            self.records.append(record)


class ScenarioResponseControls:
    """One-shot response barriers, installed only by this loopback test runner.

    The real endpoint authenticates and finishes first. For SSE, one real stage
    is sent before the barrier, so Android is already reading the response body
    when the test switches accounts. No request credentials or bodies are kept.
    """

    MODES = {"success", "http_401", "http_500", "sse_401", "sse_500"}

    def __init__(self):
        self.holds: dict[str, dict] = {}
        self.requests: list[dict] = []

    def arm(self, payload: dict) -> dict:
        identifier = payload.get("id")
        path = payload.get("path", "")
        method = payload.get("method", "GET")
        principal = payload.get("principal")
        mode = payload.get("mode", "success")
        if (not isinstance(identifier, str) or not identifier or identifier in self.holds
                or not isinstance(path, str) or not path.startswith("/api/v1/")
                or method not in {"GET", "POST"} or mode not in self.MODES
                or not isinstance(principal, str) or not principal
                or not isinstance(payload.get("late_stage", ""), str)):
            raise ValueError("Invalid or duplicate scenario hold")
        hold = {
            "id": identifier, "path": path, "method": method, "principal": principal,
            "mode": mode, "late_stage": payload.get("late_stage", ""),
            "arrived": False, "released": False, "completed": False,
            "claimed": False, "original_status": None, "frames_sent": 0,
            "late_stage_sent": False, "timed_out": False, "failure": None, "event": asyncio.Event(),
        }
        self.holds[identifier] = hold
        return self.public(hold)

    @staticmethod
    def public(hold: dict) -> dict:
        return {key: value for key, value in hold.items() if key != "event"}

    def release(self, identifier: str) -> dict:
        hold = self.holds[identifier]
        hold["released"] = True
        hold["event"].set()
        return self.public(hold)

    def reset(self) -> None:
        # An aborted case must not strand a request before the next test starts.
        for identifier in self.holds:
            self.release(identifier)
        self.holds.clear()
        self.requests.clear()

    def match(self, scope: dict) -> dict | None:
        # Gateway derives this state from a verified session. Core replaces the
        # caller's X-User-Id with the verified subject before sending a response.
        principal = scope.get("state", {}).get("principal")
        if not principal:
            principal = dict(scope.get("headers", [])).get(b"x-user-id", b"").decode()
        for hold in self.holds.values():
            if (not hold["claimed"] and hold["path"] == scope["path"]
                    and hold["method"] == scope["method"] and hold["principal"] == principal):
                hold["claimed"] = True
                return hold
        return None


class ScenarioResponseMiddleware:
    def __init__(self, app, controls: ScenarioResponseControls):
        self.app, self.controls = app, controls

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not scope.get("path", "").startswith("/api/v1/"):
            return await self.app(scope, receive, send)
        record = {
            "path": scope["path"], "method": scope["method"],
            "principal": dict(scope.get("headers", [])).get(b"x-user-id", b"").decode(),
            "status": None,
        }
        self.controls.requests.append(record)
        hold = None
        captured = []

        async def capture(message):
            nonlocal hold
            if message["type"] == "http.response.start":
                record["status"] = message["status"]
                record["principal"] = scope.get("state", {}).get("principal") or dict(
                    scope.get("headers", [])).get(b"x-user-id", b"").decode()
                hold = self.controls.match(scope)
                if hold:
                    hold["original_status"] = message["status"]
            if hold:
                captured.append(message)
            else:
                await send(message)

        await self.app(scope, receive, capture)
        if hold is None:
            return
        start = captured[0]
        original_body = b"".join(message.get("body", b"") for message in captured[1:])
        stream = b"text/event-stream" in dict(start["headers"]).get(b"content-type", b"")
        mode = hold["mode"]
        http_error = mode.startswith("http_")
        status = int(mode.rsplit("_", 1)[1]) if mode != "success" else None
        error = {"code": "SESSION_EXPIRED" if status == 401 else "SCENARIO_OLD_ERROR",
                 "message": f"OLD_ACCOUNT_ERROR_{status}", "http_status": status}

        def frame(event: str, data: dict) -> bytes:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()

        # HTTP errors wait before their headers; SSE barriers deliver a real
        # first stage and then pause a socket read in the production repository.
        if stream and not http_error:
            await send(start)
            frames = [part + b"\n\n" for part in original_body.split(b"\n\n") if part]
            first_stage = next((index for index, part in enumerate(frames) if b"event: stage\n" in part), None)
            if first_stage is not None:
                prefix = frames[:first_stage + 1]
                await send({"type": "http.response.body", "body": b"".join(prefix), "more_body": True})
                hold["frames_sent"] += len(prefix)
                original_body = b"".join(frames[first_stage + 1:])
            else:
                hold["failure"] = "expected_sse_stage_not_found"
                await send({"type": "http.response.body", "body": original_body, "more_body": False})
                hold["arrived"] = hold["completed"] = True
                record["delivered_status"] = start["status"]
                return
        elif mode.startswith("sse_"):
            hold["failure"] = "expected_event_stream_response"
            await send(start)
            await send({"type": "http.response.body", "body": original_body, "more_body": False})
            hold["arrived"] = hold["completed"] = True
            record["delivered_status"] = start["status"]
            return
        hold["arrived"] = True
        try:
            await asyncio.wait_for(hold["event"].wait(), timeout=120)
        except asyncio.TimeoutError:
            hold["timed_out"] = True
        if stream and not http_error:
            if hold["late_stage"]:
                await send({"type": "http.response.body", "body": frame("stage", {
                    "stage": "scenario_late", "label": hold["late_stage"], "phase": "started", "step": 99,
                }), "more_body": True})
                hold["late_stage_sent"] = True
                hold["frames_sent"] += 1
            body = frame("error", error) if mode.startswith("sse_") else original_body
            await send({"type": "http.response.body", "body": body, "more_body": False})
            hold["frames_sent"] += body.count(b"event: ")
        else:
            if http_error:
                start = {**start, "status": status, "headers": [(b"content-type", b"application/json")]}
                original_body = json.dumps({"detail": error}).encode()
            await send(start)
            await send({"type": "http.response.body", "body": original_body, "more_body": False})
        hold["completed"] = True
        record["delivered_status"] = status if http_error else start["status"]


def install_response_controls(app, authorize) -> ScenarioResponseControls:
    from fastapi import Header, HTTPException

    controls = ScenarioResponseControls()

    @app.post("/__scenario/hold")
    async def hold_response(payload: dict, x_scenario_token: str = Header(default="")):
        authorize(x_scenario_token)
        try:
            return controls.arm(payload)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/__scenario/release")
    async def release_response(payload: dict, x_scenario_token: str = Header(default="")):
        authorize(x_scenario_token)
        try:
            return controls.release(payload.get("id", ""))
        except KeyError as error:
            raise HTTPException(404, "Unknown scenario hold") from error

    @app.get("/__scenario/holds")
    async def read_holds(x_scenario_token: str = Header(default="")):
        authorize(x_scenario_token)
        return {"holds": [controls.public(hold) for hold in controls.holds.values()]}

    @app.get("/__scenario/requests")
    async def read_requests(x_scenario_token: str = Header(default="")):
        authorize(x_scenario_token)
        return {"requests": controls.requests}

    app.add_middleware(ScenarioResponseMiddleware, controls=controls)
    return controls


def install_receipt_controls(app, authorize) -> None:
    """Retain lookup metadata for an already-settled transfer in emulator tests.

    Successful turns do not normally retain the retry metadata. This fixture
    lets Android exercise a lost local receipt against a real completed Core
    operation. It never executes the graph, changes business status or writes
    the ledger, and cannot associate an arbitrary operation with a conversation.
    """
    from fastapi import Header, HTTPException

    @app.post("/__scenario/retain-receipt")
    def retain_receipt(payload: dict, x_scenario_token: str = Header(default="")):
        authorize(x_scenario_token)
        import conversation
        from security import SecurityError

        principal, conversation_id, operation_id = (
            payload.get(key) for key in ("principal", "conversation_id", "operation_id")
        )
        if any(not isinstance(value, str) or not value.strip() for value in (principal, conversation_id, operation_id)):
            raise HTTPException(400, "Scenario receipt target must contain nonempty identifiers")
        try:
            thread_id = conversation.conversation_thread_id(principal, conversation_id)
        except SecurityError as error:
            raise HTTPException(400, "Invalid scenario receipt target") from error
        service = conversation.SERVICE
        config = service._config(thread_id)
        with service._thread_lock(thread_id):
            graph = conversation.get_graph()
            state = dict(graph.get_state(config).values or {})
            execution = state.get("execution_result") or {}
            receipt = execution.get("data") or {}
            plan = state.get("plan") or {}
            transaction_id = receipt.get("transaction_id")
            if (state.get("user_id") != principal or state.get("business_status") != "SUCCEEDED"
                    or execution.get("type") != "TRANSFER_SUCCESS" or receipt.get("status") != "SUCCESS"
                    or not isinstance(transaction_id, str) or not transaction_id):
                raise HTTPException(409, "Conversation has no completed transfer receipt")
            matching_tasks = [task for task in plan.get("tasks", [])
                              if task.get("action") == "TRANSFER" and task.get("status") == "SUCCEEDED"
                              and task.get("idempotency_key") == operation_id
                              and (task.get("details", {}).get("result") or {}).get("transaction_id") == transaction_id]
            if len(matching_tasks) != 1:
                raise HTTPException(409, "Operation does not belong to the completed transfer")
            record = {"operation_id": operation_id, "endpoint": "transfer_execute", "result_type": "TRANSFER_SUCCESS"}
            if state.get("reconciliation") not in (None, record):
                raise HTTPException(409, "Conversation already retains a different operation")
            graph.update_state(config, {"reconciliation": record})
        return {"operation_id": operation_id}


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
        "BANK_LEDGER_STORE_PATH": str(folder / "ledger.json"),
        "BANK_DEVICE_STORE_PATH": str(folder / "devices.json"),
        "BANK_AUDIT_LOG_PATH": "", "BANK_AUDIT_LOG_DIR": str(folder),  # -> {service}-audit.jsonl
        "BANK_AGENT_RATE_LIMIT_PER_MINUTE": "0",
        "BANK_RATE_LIMIT_PER_MINUTE": "0",
        "BANK_SESSION_TTL_SECONDS": "86400",
        "BANK_ADMIN_AUDIT_TOKEN": token,
        "BANK_FEISHU_ENABLED": "0",
        "BANK_LOGIN_REQUIRE_OTP": "0",
        "BANK_MFA_TEST_OTP": "888888",
        "BANK_MFA_DYNAMIC_OTP": "0",  # deterministic legacy fixtures; explicit issuance still binds a dynamic code
        "BANK_REQUIRE_CLIENT_AUTH": "0",
    })
    if args.mode == "offline":
        os.environ["DEEPSEEK_API_KEY"] = ""
    module = importlib.import_module("main" if args.service == "core" else "agent_api")
    app = module.app
    source_root = Path(args.source_dir).resolve() if args.source_dir else ROOT
    sources = ["agent.py", "agent_api.py", "conversation.py", "main.py", "planner.py", "security.py", "bank_tools.py", "sandbox.py", "intent_language.py", "workflow_contract.py", "workflow_runtime.py", "banking_knowledge.py"]
    sources += sorted(path.relative_to(source_root).as_posix() for package in ("bank_core", "bank_agent", "bank_security", "bank_planning")
                      for path in (source_root / package).rglob("*.py"))
    sources += sorted(path.relative_to(source_root).as_posix() for path in (source_root / "bank_security").rglob("*.py"))
    fingerprints = {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest() for name in sources}
    (folder / f"{args.service}-run.json").write_text(json.dumps({
        "service": args.service, "mode": args.mode, "source_root": str(source_root),
        "started_at": datetime.now().astimezone().isoformat(), "source_sha256": fingerprints,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.service == "core":
        # Only this runner knows the reset route; it authenticates separately
        # with the isolated control token, before any demo session exists.
        from bank_core import http_edge

        http_edge._SESSION_BOOTSTRAP_PATHS.add("/__scenario/reset")
    from fastapi import Header, HTTPException
    records: list[dict] = []
    model_calls: list[dict] = []

    def authorize(value: str) -> None:
        import hmac
        if not hmac.compare_digest(value, token):
            raise HTTPException(403, "Scenario control token required")

    response_controls = install_response_controls(app, authorize)

    if args.service == "gateway":
        import conversation
        from bank_agent import runtime
        install_receipt_controls(app, authorize)
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
        original_approval_stream = conversation.SERVICE.resolve_approval_stream

        def recording_approval_stream(**kwargs):
            started = time.monotonic()
            for event in original_approval_stream(**kwargs):
                if event.get("event") == "error":
                    records.append({
                        "event": "stream_error", "transport_status": 200,
                        "http_status": event["data"].get("http_status"),
                        "response": {"detail": event["data"]},
                        "input": f"[{kwargs['decision']}]", "observation_index": len(records) + 1,
                        "elapsed_ms": round((time.monotonic() - started) * 1000),
                    })
                    with (folder / "turns.jsonl").open("a", encoding="utf-8") as output:
                        output.write(json.dumps(records[-1], ensure_ascii=False) + "\n")
                yield event
        conversation.SERVICE.resolve_approval_stream = recording_approval_stream
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
        if runtime.llm is not None:
            runtime.llm = ObservedScenarioModel(runtime.llm, model_calls)

    @app.post("/__scenario/reset")
    async def reset(payload: dict, x_scenario_token: str = Header(default="")):
        authorize(x_scenario_token)
        records.clear()
        model_calls.clear()
        response_controls.reset()
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
            if payload.get("recurring_transactions"):
                # Unknown merchant: prove detection uses ledger evidence rather
                # than merely returning the two already registered mandates.
                for index in range(3):
                    module.TRANSACTIONS.append({
                        "tx_id": f"TX-RECURRING-{index}", "account_id": "user_zhang",
                        "merchant": "音乐空间会员", "category": "娱乐", "amount": 18,
                        "time": (now - timedelta(days=30 * index)).strftime("%Y-%m-%d %H:%M:%S"),
                        "card_id": "CARD-001",
                    })
            next(row for row in module.TRANSACTIONS if row["tx_id"] == "TX-SCENARIO-FOREIGN")["country"] = "US"
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

    @app.post("/__scenario/advance-due")
    def advance_due(x_scenario_token: str = Header(default="")):
        """Drive the real scheduler at a test time without changing bank clocks."""
        authorize(x_scenario_token)
        if args.service != "core":
            raise HTTPException(404, "Core-only scenario control")
        from datetime import timezone
        from bank_core.routes.tasks import run_due_tasks

        original_ready = module.TASK_STORE.ready
        module.TASK_STORE.ready = lambda: original_ready(now=datetime(2100, 1, 1, tzinfo=timezone.utc))
        try:
            return run_due_tasks(x_admin_token=token, x_request_id="scenario-advance-due")
        finally:
            module.TASK_STORE.ready = original_ready

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
