"""Evaluate Android scenario observations without calling any banking service.

Exit codes: 0 = all declared cases/turns passed, 1 = evaluation failed,
2 = invalid input or an I/O error. The case manifest is the source of truth.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
from pathlib import Path
import sys
from typing import Any
import unicodedata

STATE_FIELDS = ("balance", "locked_amount", "transactions", "subscriptions", "cards", "scheduled_transfers", "splits")
EXPECT_KEYS = {"approval", "parsed", "state", "reply_any", "reply_all", "no_claim", "unchanged", "balance_delta", "locked_delta", "new_approval", "time_contains", "http_error"}
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
LABELS = {"PASS": "通过", "FAIL": "不符合预期", "ERROR": "观测错误", "NOT_RUN": "未录制"}


def number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def same(expected: Any, actual: Any) -> bool:
    """JSON equality, except bool is never equal to a numeric zero or one."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(same(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(same(left, right) for left, right in zip(expected, actual))
    if number(expected) is not None and number(actual) is not None:
        return number(expected) == number(actual)
    return type(expected) is type(actual) and expected == actual


def subset_mismatches(expected: Any, actual: Any, path: str = "parsed") -> list[dict]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        issues = []
        for key, value in expected.items():
            if key not in actual:
                issues.append({"path": f"{path}.{key}", "expected": value, "actual": None, "missing": True})
            else:
                issues.extend(subset_mismatches(value, actual[key], f"{path}.{key}"))
        return issues
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [issue for index, (left, right) in enumerate(zip(expected, actual))
                for issue in subset_mismatches(left, right, f"{path}[{index}]")]
    return [] if same(expected, actual) else [{"path": path, "expected": expected, "actual": actual}]


def omit_null_members(value: Any) -> Any:
    # Android Gson omits null object members when persisting ChatMessage.
    if isinstance(value, dict):
        return {key: omit_null_members(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [omit_null_members(item) for item in value]
    return value


def projected_reply(reply: str, result: Any) -> str:
    """The explicit displayText projection in ChatViewModel, not Markdown stripping."""
    result = result if isinstance(result, dict) else {}
    execution = result.get("execution_result")
    execution = execution if isinstance(execution, dict) else {}
    lines = reply.splitlines()
    if execution.get("type") in {"WEALTH_QUERY", "WEALTH_RECOMMEND", "WEALTH_COMPARE"}:
        lines = [line for line in lines if not line.lstrip().startswith("- `")]
    if result.get("profile_result") is not None:
        prefixes = ("✅ 风险测评完成", "特点：", "匹配理财产品级别：")
        lines = [line for line in lines if not line.startswith(prefixes)]
    return "\n".join(line for line in lines if line.strip())


def card_values(result: Any) -> list[str]:
    """Values in result cards; hidden planner metadata cannot satisfy reply checks."""
    def values(value):
        if isinstance(value, dict):
            return [text for item in value.values() for text in values(item)]
        if isinstance(value, list):
            return [text for item in value for text in values(item)]
        return [str(value)] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else []
    if not isinstance(result, dict):
        return []
    return [text for key in ("execution_result", "execution_results", "profile_result", "risk_quiz", "chart_data", "todo_card")
            for text in values(result.get(key))]


def valid_state(state: Any) -> bool:
    return (isinstance(state, dict) and all(key in state for key in STATE_FIELDS)
            and all(number(state[key]) is not None for key in ("balance", "locked_amount"))
            and all(isinstance(state[key], list) for key in STATE_FIELDS[2:]))


def state_comparison(before: Any, after: Any) -> dict:
    if not valid_state(before) or not valid_state(after):
        return {"available": False}
    changes = {key: {"before": before.get(key), "after": after.get(key)}
               for key in sorted(before.keys() | after.keys()) if not same(before.get(key), after.get(key))}
    return {"available": True, "balance_delta": float(number(after["balance"]) - number(before["balance"])),
            "locked_delta": float(number(after["locked_amount"]) - number(before["locked_amount"])), "changes": changes}


def local_times(parsed: dict) -> dict:
    output = {}
    for field in ("scheduled_at", "event_at"):
        raw = parsed.get(field)
        if not isinstance(raw, str) or not raw:
            continue
        try:
            date = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        date = date.replace(tzinfo=LOCAL_TIMEZONE) if date.tzinfo is None else date.astimezone(LOCAL_TIMEZONE)
        output[field] = {"raw": raw, "asia_shanghai": date.isoformat()}
    return output


def evaluate_http_refusal(spec: dict, observation: Any, step: int, baseline: Any, previous: Any, seen_records: set) -> dict:
    """A rejected request must be visibly rejected and leave all observed state intact."""
    expect = spec.get("expect", {})
    checks = []

    def check(name, passed, wanted, actual, kind="expectation"):
        checks.append({"name": name, "passed": bool(passed), "expected": wanted, "actual": actual, "kind": kind})

    result = {"step": step, "input": spec.get("input"), "action": spec.get("action"), "expected": expect, "checks": checks}
    if not isinstance(observation, dict):
        check("recording.turn", False, "完整拒绝观测", observation, "recording")
        return {**result, "status": "NOT_RUN" if observation is None else "ERROR", "actual": None}
    check("recording.ui_error", "ui_error" not in observation, "界面操作成功", observation.get("ui_error"), "recording")
    field = "action" if "action" in spec else "input"
    check("recording.input", same(spec[field], observation.get(field)), spec[field], observation.get(field), "recording")
    check("recording.expected", same(expect, observation.get("expected")), expect, observation.get("expected"), "recording")
    server = observation.get("server") or {}
    response = server.get("response") or {}
    detail = response.get("detail") if isinstance(response, dict) else None
    detail = detail if isinstance(detail, dict) else {}
    displayed = observation.get("displayed_error")
    check("recording.event", server.get("event") == "http_error", "http_error", server.get("event"), "recording")
    actual_input = server.get("input")
    input_matches = actual_input in {"[approve]", "[challenge]"} if "action" in spec else (
        isinstance(actual_input, str) and unicodedata.normalize("NFKC", actual_input).strip() == unicodedata.normalize("NFKC", spec["input"]).strip())
    check("recording.server_input", input_matches, spec[field], actual_input, "recording")
    index = server.get("observation_index")
    identity = ("http_error", index)
    check("recording.unique_response", type(index) is int and index > 0 and identity not in seen_records,
          "本案唯一的 HTTP 观测序号", index, "recording")
    seen_records.add(identity)
    check("recording.visible_refusal", observation.get("error_visible") is True and isinstance(displayed, str)
          and bool(displayed) and displayed == detail.get("message"), detail.get("message"), displayed, "recording")
    before_messages, after_messages = observation.get("messages_before"), observation.get("messages_after")
    transcripts_valid = isinstance(before_messages, list) and isinstance(after_messages, list)
    old_ids = {row.get("id") for row in before_messages if isinstance(row, dict)} if transcripts_valid else set()
    new_assistants = [row for row in after_messages if isinstance(row, dict) and row.get("role") == "ASSISTANT" and row.get("id") not in old_ids] if transcripts_valid else None
    check("no_new_success_reply", transcripts_valid and not new_assistants, "拒绝显示为错误提示，不生成成功回复", new_assistants)
    incremental, cumulative = state_comparison(previous, observation.get("after")), state_comparison(baseline, observation.get("after"))
    check("refusal_has_not_executed", incremental["available"] and not incremental.get("changes"), "全部已观测账务不变", incremental)
    for key, wanted in expect.items():
        if key == "http_error":
            actual = {"status": server.get("http_status"), "code": detail.get("code")}
            check(key, isinstance(wanted, dict) and not subset_mismatches(wanted, actual, "http_error"), wanted, actual)
        elif key == "unchanged":
            check(key, wanted is True and incremental["available"] and not incremental.get("changes"), wanted, incremental)
        elif key == "reply_any":
            matches = [word for word in wanted if isinstance(displayed, str) and word in displayed] if isinstance(wanted, list) else []
            check(key, bool(matches), wanted, displayed)
        else:
            check(f"expectation.{key}", False, "拒绝路径仅支持 http_error/unchanged/reply_any", wanted, "recording")
    failures = [item for item in checks if not item["passed"]]
    result.update(status="ERROR" if any(item["kind"] == "recording" for item in failures) else "FAIL" if failures else "PASS", actual={
        "event": server.get("event"), "status": server.get("http_status"), "approval_id": None,
        "request_id": response.get("request_id"), "conversation_id": server.get("conversation_id"), "parsed": {}, "times": {},
        "server_reply": response, "displayed_reply": displayed, "displayed_result": None,
        "elapsed_ms": server.get("elapsed_ms"), "model_calls": observation.get("model_calls"),
        "state_from_previous": incremental, "state_from_original_before": cumulative,
    })
    return result


def evaluate_turn(spec: dict, observation: Any, step: int, baseline: Any, previous: Any,
                  prior_approval: str | None, seen_messages: set, seen_records: set) -> dict:
    expect = spec.get("expect", {})
    if "http_error" in expect:
        return evaluate_http_refusal(spec, observation, step, baseline, previous, seen_records)
    checks: list[dict] = []

    def check(name, passed, expected, actual, kind="expectation"):
        checks.append({"name": name, "passed": bool(passed), "expected": expected, "actual": actual, "kind": kind})

    result = {"step": step, "input": spec.get("input"), "action": spec.get("action"), "expected": expect, "checks": checks}
    if observation is None:
        check("recording.turn", False, "完整的当前轮观测", None, "recording")
        return {**result, "status": "NOT_RUN", "actual": None}
    if not isinstance(observation, dict):
        check("recording.turn", False, "JSON 对象", observation, "recording")
        return {**result, "status": "ERROR", "actual": observation}
    if "ui_error" in observation:
        check("recording.ui_error", False, "UI 操作与录制均成功", observation["ui_error"], "recording")
    field = "action" if "action" in spec else "input"
    check("recording.input", same(spec[field], observation.get(field)), spec[field], observation.get(field), "recording")
    check("recording.expected", same(expect, observation.get("expected")), expect, observation.get("expected"), "recording")
    server = observation.get("server")
    server = server if isinstance(server, dict) else {}
    envelope = server.get("actual")
    envelope = envelope if isinstance(envelope, dict) else {}
    displayed = observation.get("displayed_reply")
    displayed = displayed if isinstance(displayed, dict) else {}
    parsed = server.get("parsed")
    parsed = parsed if isinstance(parsed, dict) else {}
    approval = envelope.get("approval")
    approval_id = approval.get("approval_id") if isinstance(approval, dict) else None
    approval_id = approval_id if isinstance(approval_id, str) and approval_id else None
    is_approval = envelope.get("status") == "APPROVAL_REQUIRED" and approval_id is not None
    expected_input = "[reject]" if spec.get("action") == "reject" else "[approve]" if "action" in spec else spec["input"]
    server_input = server.get("input")
    # The gateway records sanitize_text's NFKC + strip projection. Keep the
    # actual typed input and copied expectation checks above strictly exact.
    normalized_input_matches = (isinstance(server_input, str)
                                and unicodedata.normalize("NFKC", expected_input).strip()
                                == unicodedata.normalize("NFKC", server_input).strip())
    check("recording.server_input", normalized_input_matches, expected_input, server_input, "recording")
    expected_event = "approval_action" if "action" in spec else "approval" if is_approval else "result"
    check("recording.event", server.get("event") == expected_event, expected_event, server.get("event"), "recording")
    check("recording.response", envelope.get("code") == 200 and envelope.get("status") in {"COMPLETED", "APPROVAL_REQUIRED"},
          "code=200 且为已完成/待审批终态", {key: envelope.get(key) for key in ("code", "status", "message", "detail")}, "recording")
    consistent_approval = is_approval if envelope.get("status") == "APPROVAL_REQUIRED" else envelope.get("approval") is None
    check("recording.approval", consistent_approval, "审批状态与审批对象一致", approval, "recording")
    check("recording.parsed", bool(parsed), "完整解析状态", parsed, "recording")
    request_id, conversation_id = envelope.get("request_id"), envelope.get("conversation_id")
    identity = (request_id, server.get("event"), server.get("input"))
    valid_identity = isinstance(request_id, str) and bool(request_id) and isinstance(conversation_id, str) and bool(conversation_id)
    check("recording.server_identity", valid_identity and identity not in seen_records,
          "有效 request_id/conversation_id；无重复录制", list(identity), "recording")
    if valid_identity:
        seen_records.add(identity)
    message_id = displayed.get("id")
    valid_message = isinstance(message_id, str) and bool(message_id)
    check("recording.ui_message", displayed.get("role") == "ASSISTANT" and valid_message and message_id not in seen_messages,
          "本轮新增的 ASSISTANT 消息", {key: displayed.get(key) for key in ("id", "role")}, "recording")
    if valid_message:
        seen_messages.add(message_id)
    if "action" not in spec or displayed.get("traceId") is not None:
        check("recording.trace", valid_identity and displayed.get("traceId") == request_id, request_id, displayed.get("traceId"), "recording")
    reply, ui_reply = envelope.get("reply"), displayed.get("text")
    projected = projected_reply(reply, envelope.get("result")) if isinstance(reply, str) else None
    check("recording.ui_text", isinstance(reply, str) and bool(reply.strip()) and isinstance(ui_reply, str) and ui_reply == projected,
          projected, ui_reply, "recording")
    check("recording.ui_result", same(omit_null_members(envelope.get("result")), omit_null_members(displayed.get("result"))),
          omit_null_members(envelope.get("result")), displayed.get("result"), "recording")
    check("recording.baseline", valid_state(baseline), "包含所有账务观测字段的原始 before", list(baseline) if isinstance(baseline, dict) else None, "recording")
    after = observation.get("after")
    check("recording.after", valid_state(after), "包含所有账务观测字段的 after", list(after) if isinstance(after, dict) else None, "recording")
    incremental = state_comparison(previous, after)
    cumulative = state_comparison(baseline, after)
    check("recording.previous_state", incremental["available"], "上一轮 after（首轮为 before）可比较", incremental["available"], "recording")
    # An approval card is never evidence that a monetary/card operation executed.
    if is_approval:
        check("approval_has_not_executed", incremental["available"] and not incremental.get("changes"),
              "等待确认时账务状态不变", incremental, "expectation")
    ui_text = "\n".join(([ui_reply] if isinstance(ui_reply, str) else []) + card_values(displayed.get("result")))
    all_claims = "\n".join(([reply] if isinstance(reply, str) else []) + [ui_text])
    times = local_times(parsed)
    for key, wanted in expect.items():
        if key not in EXPECT_KEYS:
            check(f"expectation.{key}", False, "受支持的预期字段", wanted, "recording")
        elif key == "approval":
            check(key, isinstance(wanted, bool) and wanted == is_approval, wanted, is_approval)
        elif key == "parsed":
            mismatches = subset_mismatches(wanted, parsed)
            check(key, isinstance(wanted, dict) and not mismatches, wanted, {"parsed": parsed, "mismatches": mismatches})
        elif key == "state":
            mismatches = subset_mismatches(wanted, after, "after")
            check(key, isinstance(wanted, dict) and not mismatches, wanted, {"after": after, "mismatches": mismatches})
        elif key in {"reply_any", "reply_all", "no_claim"}:
            valid = isinstance(wanted, list) and bool(wanted) and all(isinstance(text, str) and bool(text) for text in wanted)
            matches = [text for text in wanted if text in (all_claims if key == "no_claim" else ui_text)] if valid else []
            passed = bool(matches) if key == "reply_any" else len(matches) == len(wanted) if key == "reply_all" and valid else not matches
            check(key, valid and passed, wanted, {"matched": matches, "ui_text_and_cards": ui_text})
        elif key == "unchanged":
            unchanged = incremental["available"] and not incremental.get("changes")
            check(key, isinstance(wanted, bool) and incremental["available"] and wanted == unchanged, wanted, incremental)
        elif key in {"balance_delta", "locked_delta"}:
            actual = incremental.get(key)
            check(key, number(wanted) is not None and number(actual) == number(wanted), wanted, actual)
        elif key == "new_approval":
            changed = bool(prior_approval and approval_id and prior_approval != approval_id and is_approval)
            check(key, isinstance(wanted, bool) and bool(prior_approval) and wanted == changed,
                  wanted, {"previous_approval_id": prior_approval, "approval_id": approval_id})
        elif key == "time_contains":
            check(key, isinstance(wanted, str) and bool(wanted) and any(wanted in value["asia_shanghai"] for value in times.values()), wanted, times)
    failures = [item for item in checks if not item["passed"]]
    status = "ERROR" if any(item["kind"] == "recording" for item in failures) else "FAIL" if failures else "PASS"
    result.update({"status": status, "actual": {
        "event": server.get("event"), "status": envelope.get("status"), "approval_id": approval_id,
        "request_id": request_id, "conversation_id": conversation_id, "parsed": parsed,
        "server_reply": reply, "displayed_reply": ui_reply, "displayed_result": displayed.get("result"),
        "times": times, "elapsed_ms": server.get("elapsed_ms"), "model_calls": observation.get("model_calls"),
        "state_from_previous": incremental, "state_from_original_before": cumulative,
        "ui_error": observation.get("ui_error"), "server_records_on_error": observation.get("server_records"),
    }})
    return result


def evaluate_cases(cases: Any, observations: Any) -> dict:
    if not isinstance(cases, list) or not cases:
        raise ValueError("用例文件必须是非空 JSON 数组")
    if not isinstance(observations, list):
        raise ValueError("观测文件必须是 JSON 数组")
    identifiers = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"] or case["id"] in identifiers:
            raise ValueError("用例必须含唯一、非空的字符串 id")
        identifiers.add(case["id"])
        if not isinstance(case.get("direction"), str) or not isinstance(case.get("turns"), list) or not case["turns"]:
            raise ValueError(f"{case['id']} 缺少 direction 或非空 turns")
        for turn in case["turns"]:
            if (not isinstance(turn, dict) or ("input" in turn) == ("action" in turn)
                    or not isinstance(turn.get("expect", {}), dict)
                    or ("input" in turn and (not isinstance(turn["input"], str) or not turn["input"]))
                    or ("action" in turn and turn["action"] not in {"approve", "approve_red", "approve_red_wrong_otp", "reject"})):
                raise ValueError(f"{case['id']} 包含无效轮次（input/action 二选一，expect 必须是对象）")
    by_id: dict[str, list] = {}
    coverage_errors = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict) or not isinstance(observation.get("id"), str):
            coverage_errors.append(f"第 {index + 1} 条观测无有效 id")
            continue
        identifier = observation["id"]
        by_id.setdefault(identifier, []).append(observation)
        if identifier not in identifiers:
            coverage_errors.append(f"观测包含用例清单之外的案例 {identifier}")
    evaluated = []
    for case in cases:
        records = by_id.get(case["id"], [])
        observation = records[0] if records else {}
        errors = []
        if len(records) > 1:
            errors.append("案例观测重复，不能选择其中一次作为通过证据")
        if "setup_error" in observation:
            errors.append(f"初始化失败：{observation['setup_error']}")
        if records and observation.get("direction") != case["direction"]:
            errors.append("观测 direction 与用例清单不同")
        observed_turns = observation.get("turns", [])
        if not isinstance(observed_turns, list):
            errors.append("观测 turns 不是数组")
            observed_turns = []
        indexed: dict[int, list] = {}
        for position, turn in enumerate(observed_turns, 1):
            step = turn.get("step") if isinstance(turn, dict) else None
            if type(step) is not int or not 1 <= step <= len(case["turns"]):
                errors.append(f"观测轮次编号无效或超出清单：{step!r}")
                continue
            indexed.setdefault(step, []).append(turn)
            if position != step:
                errors.append(f"观测第 {position} 条的 step={step}，顺序不完整")
        baseline = observation.get("before")
        previous, prior_approval = baseline, None
        seen_messages, seen_records = set(), set()
        turns = []
        for step, spec in enumerate(case["turns"], 1):
            candidates = indexed.get(step, [])
            row = candidates[0] if len(candidates) == 1 else None
            if len(candidates) > 1:
                errors.append(f"第 {step} 轮重复录制")
            result = evaluate_turn(spec, row, step, baseline, previous, prior_approval, seen_messages, seen_records)
            turns.append(result)
            previous = row.get("after") if isinstance(row, dict) else None
            actual = result.get("actual")
            prior_approval = actual.get("approval_id") if isinstance(actual, dict) else None
        status = "ERROR" if errors or any(turn["status"] == "ERROR" for turn in turns) else (
            "NOT_RUN" if all(turn["status"] == "NOT_RUN" for turn in turns) else
            "FAIL" if any(turn["status"] != "PASS" for turn in turns) else "PASS")
        evaluated.append({"id": case["id"], "direction": case["direction"], "status": status,
                          "errors": errors, "turns": turns})
    case_counts = Counter(case["status"] for case in evaluated)
    turn_counts = Counter(turn["status"] for case in evaluated for turn in case["turns"])
    directions = {}
    for case in evaluated:
        group = directions.setdefault(case["direction"], {"cases": 0, "cases_passed": 0, "turns": 0, "turns_passed": 0})
        group["cases"] += 1
        group["cases_passed"] += case["status"] == "PASS"
        group["turns"] += len(case["turns"])
        group["turns_passed"] += sum(turn["status"] == "PASS" for turn in case["turns"])
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "passed": not coverage_errors and case_counts["PASS"] == len(cases),
            "summary": {"cases_total": len(cases), "turns_total": sum(turn_counts.values()),
                        "cases": {key: case_counts[key] for key in LABELS}, "turns": {key: turn_counts[key] for key in LABELS},
                        "directions": directions}, "coverage_errors": coverage_errors, "cases": evaluated}


def render_markdown(report: dict) -> str:
    def escape(value):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)
        return html.escape(text).replace("|", "&#124;").replace("\n", "<br>")

    def block(value):
        # HTML escapes prevent recorded Markdown/backticks from changing report structure.
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        return "<pre>" + html.escape(text) + "</pre>"

    summary = report["summary"]
    lines = ["# Android 自然语言对话逐轮评估", "", f"生成时间：{report['generated_at']}", "",
             f"总体：{'通过' if report['passed'] else '未通过'}。案例 {summary['cases']['PASS']}/{summary['cases_total']} 通过；"
             f"轮次 {summary['turns']['PASS']}/{summary['turns_total']} 通过。", "",
             "观测错误和未录制轮次均计入分母，不作为通过。资产增量按相邻轮比较；累计变化始终对照原始 before。"
             "等待审批的轮次还必须满足账务状态未变。时间以 Asia/Shanghai（UTC+08:00）比较。", "",
             "| 方向 | 通过案例 / 总案例 | 通过轮次 / 总轮次 |", "| --- | --- | --- |"]
    for direction, counts in summary["directions"].items():
        lines.append(f"| {escape(direction)} | {counts['cases_passed']} / {counts['cases']} | {counts['turns_passed']} / {counts['turns']} |")
    if report["coverage_errors"]:
        lines.extend(["", "## 清单与录制覆盖错误", "", *[f"- {escape(error)}" for error in report["coverage_errors"]]])
    for case in report["cases"]:
        lines.extend(["", f"## {escape(case['id'])} · {escape(case['direction'])} · {LABELS[case['status']]}", ""])
        if case["errors"]:
            lines.extend([*[f"- {escape(error)}" for error in case["errors"]], ""])
        for turn in case["turns"]:
            lines.extend([f"### 第 {turn['step']} 轮 · {LABELS[turn['status']]}", "",
                          f"{'界面操作' if turn['action'] else '用户输入'}：{escape(turn['action'] or turn['input'])}", "",
                          "预期：", "", block(turn["expected"]), ""])
            actual = turn.get("actual")
            if isinstance(actual, dict):
                lines.extend(["界面实际回复：", "", block(actual["displayed_reply"]), "",
                              "服务端原始回复：", "", block(actual["server_reply"]), "",
                              "实际状态与解析：", "", block({key: actual[key] for key in ("event", "status", "approval_id", "parsed", "times")}), "",
                              "账务变化：", "", block({"相邻轮": actual["state_from_previous"], "原始 before 至当前": actual["state_from_original_before"]}), ""])
            lines.extend(["| 校验 | 结论 | 预期 | 实际 |", "| --- | --- | --- | --- |"])
            for check in turn["checks"]:
                # Large result objects are retained in JSON and the UI result details below.
                if check["passed"] and check["kind"] == "recording":
                    continue
                lines.append(f"| {escape(check['name'])} | {'通过' if check['passed'] else '失败'} | {escape(check['expected'])} | {escape(check['actual'])} |")
            if isinstance(actual, dict) and actual.get("displayed_result") is not None:
                lines.extend(["", "<details><summary>界面附属结果数据</summary>", "", block(actual["displayed_result"]), "", "</details>", ""])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        sources = {"cases": args.cases, "observations": args.observations}
        raw = {key: path.read_bytes() for key, path in sources.items()}
        def reject_constant(value):
            raise ValueError(f"JSON 不允许非有限数值 {value}")
        data = {key: json.loads(value.decode("utf-8-sig"), parse_constant=reject_constant) for key, value in raw.items()}
        report = evaluate_cases(data["cases"], data["observations"])
        report["sources"] = {key: {"path": str(path.resolve()), "sha256": hashlib.sha256(raw[key]).hexdigest()} for key, path in sources.items()}
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (args.output_dir / "evaluation.md").write_text(render_markdown(report), encoding="utf-8")
    except (OSError, ValueError, TypeError) as error:
        print(f"Evaluation input/output error: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"passed": report["passed"], "summary": report["summary"], "output_dir": str(args.output_dir.resolve())}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
