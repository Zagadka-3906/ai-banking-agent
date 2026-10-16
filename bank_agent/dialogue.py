"""Multi-turn context: slot answers, ellipsis, compound requests and pending clarifications."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional


from planner import (
    GREEN_INTENTS,
    Intent,
    PlanExecutor,
    Task,
    TaskPlan,
    build_task_plan,
    needs_clarification,
    validate_intent_payload,
)
from intent_language import ambiguous_amount, business_text, split_tasks
from bank_agent.state import (
    BankingAgentState,
)
from bank_agent.extraction import (
    amount_from_text,
    scheduled_at_from_text,
)
from bank_agent.parsing import (
    local_parse,
    parsed_payload,
)
from bank_agent.settlement import (
    consumed_task_ids,
    plan_from_state,
)


_BARE_AMOUNT_RE = re.compile(r"^\s*[¥￥]?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:元|块|人民币)?\s*$")
_BARE_CHINESE_AMOUNT_RE = re.compile(r"^\s*([零〇一二两三四五六七八九十百千万亿]+)\s*(?:元|块|人民币)?\s*$")


def _slot_answer(text: str, slot: str, parsed: Mapping[str, Any]) -> Any:
    """Interpret a bare answer in the type of the slot that was asked for.

    After a clarification question the user often replies with only a value:
    "3000" for a card limit, "下周六" for a date, "李四" for a recipient.  The
    deterministic parser needs keywords to recognise a bare number as money, so
    the pending slot supplies the missing context here.
    """

    if slot == "amount":
        if ambiguous_amount(text):
            return None
        existing = parsed.get("amount")
        if existing not in (None, 0):
            return existing
        match = _BARE_AMOUNT_RE.match(text)
        if match:
            try:
                value = round(float(match.group(1).replace(",", "")), 2)
            except ValueError:
                return None
            return value if 0 < value <= 100_000_000 else None
        chinese = _BARE_CHINESE_AMOUNT_RE.match(text)
        if chinese:
            # The pending slot supplies the omitted yuan unit, not permission
            # to turn an ambiguous "两三百" into an exact 300-yuan transfer.
            return amount_from_text(chinese.group(1) + "元")
        return None
    if slot in {"scheduled_at", "event_at"}:
        stated = parsed.get("scheduled_at") or parsed.get("event_at")
        if stated:
            return stated
        # A bare date answer ("10月5号", "下周六") carries no intent keyword, so
        # the pending question is the only thing that makes it a date.
        return scheduled_at_from_text(text)
    if slot == "target":
        return parsed.get("target")
    if slot == "participants":
        return list(parsed.get("participants") or []) or None
    if slot == "product_ids":
        return list(parsed.get("product_ids") or []) or None
    if slot in {"loss_tolerance", "experience", "horizon_years"}:
        slots = parsed.get("slots")
        if not isinstance(slots, Mapping):
            slots = {}
        return slots.get(slot)
    return None


ELLIPSIS_REPEAT = ("再转一次", "再来一次", "再来一笔", "重复上次", "照上次", "和上次一样", "跟上次一样", "同样金额", "一样的金额", "同样的金额")
ELLIPSIS_SAME_TARGET = ("上次那个", "还是那个", "还是上次", "原来那个", "同一个", "那个收款人", "那个人")
ELLIPSIS_CANCEL_IT = ("取消它", "把它取消", "退掉它", "取消掉", "不要了")
ELLIPSIS_SAME_PRODUCT = ("那个产品", "还是那只", "同一只", "换成那个")


def resolve_ellipsis(parsed: dict[str, Any], prior: Mapping[str, Any], text: str, last_result: Optional[dict] = None) -> dict[str, Any]:
    """Resolve explicit back-references from the previous turn's context.

    Only phrases that clearly point backwards ("再转一次", "还是上次那个",
    "把它取消掉") are resolved; a repeated request still goes through a fresh
    risk precheck and approval card, so nothing is ever silently re-executed.
    """

    prior_intent = str(prior.get("intent") or "")
    if not prior_intent or prior_intent == Intent.UNKNOWN.value:
        return parsed
    if any(phrase in text for phrase in ELLIPSIS_REPEAT):
        repeated = parsed_payload(
            prior_intent,
            text=text,
            amount=prior.get("amount"),
            target=prior.get("target"),
            product_ids=list(prior.get("product_ids") or []),
            slots=dict(prior.get("slots") or {}),
            participants=list(prior.get("participants") or []),
            scope=str(prior.get("date_scope") or "ALL"),
            scheduled_at=prior.get("scheduled_at"),
        )
        repeated["date_range"] = prior.get("date_range")
        # An explicit correction always wins over the referenced prior amount.
        for key in ("amount", "target", "product_ids", "scheduled_at", "event_at"):
            if parsed.get(key) not in (None, "", [], {}):
                repeated[key] = parsed[key]
        return repeated
    updated = dict(parsed)
    bill_intents = {Intent.BILL_QUERY.value, Intent.BILL_ANALYSIS.value}
    scope_explicit = bool(re.search(r"今天|今日|本月|这个月|上月|上个月|今年|去年|年度|全年|全部|所有历史", business_text(text)) or parsed.get("date_scope") == "CUSTOM")
    if prior_intent in bill_intents:
        # A date-only follow-up still asks about the same ledger, never ALL by default.
        if updated.get("intent") == Intent.UNKNOWN.value and (
            scope_explicit or (updated.get("slots") or {}).get("category") or any(word in text for word in ("按类别", "分类", "再看一遍", "整体呢", "那笔", "里面"))
        ):
            updated["intent"] = Intent.BILL_ANALYSIS.value if (updated.get("slots") or {}).get("category") or any(word in text for word in ("分析", "分类", "类别", "怎么回事")) else prior_intent
            updated["confidence"] = None
        if updated.get("intent") in bill_intents and not scope_explicit:
            updated["date_scope"] = prior.get("date_scope") or "ALL"
            updated["date_range"] = prior.get("date_range")
    # Resolve a literal ordinal only against the user's comparison order, or
    # a singular holding returned by Core. Never select a product from a
    # multi-product list merely because the user said "这个".
    candidates = list(prior.get("product_ids") or [])
    reference = re.search(r"前一个|第一个|后一个|第二个|这个产品|这只产品", text)
    if not candidates and prior_intent == Intent.WEALTH_HOLDINGS.value:
        candidates = list(prior.get("verified_product_ids") or [])
    if not candidates and prior_intent == Intent.WEALTH_HOLDINGS.value and last_result and last_result.get("type") == prior_intent:
        holdings = (last_result.get("data") or {}).get("data") or []
        if isinstance(holdings, list):
            candidates = list(dict.fromkeys(row.get("product_id") for row in holdings
                                           if isinstance(row, dict) and row.get("product_id")))
    if reference and candidates and not updated.get("target"):
        selected = None
        if prior_intent == Intent.WEALTH_COMPARE.value and reference.group() in {"前一个", "第一个", "后一个", "第二个"} and len(candidates) == 2:
            selected = candidates[0 if reference.group() in {"前一个", "第一个"} else 1]
        elif len(candidates) == 1:
            selected = candidates[0]
        if selected:
            if re.search(r"申购|买|投", text):
                updated["intent"] = Intent.WEALTH_PURCHASE.value
            elif "赎回" in text:
                updated["intent"] = Intent.WEALTH_REDEEM.value
            if updated.get("intent") in {Intent.WEALTH_PURCHASE.value, Intent.WEALTH_REDEEM.value}:
                updated["target"] = selected
                updated["product_ids"] = [selected]
                updated["confidence"] = None
                updated["parse_meta"] = {"source": "rules_context", "resolution": "explicit_reference", "reason_codes": [], "fast_path_eligible": False}
    subscription_intents = {Intent.SUBSCRIPTION_QUERY.value, Intent.SUBSCRIPTION_CANCEL.value}
    if prior_intent in subscription_intents and any(word in text for word in ("续费", "下次扣", "什么时候扣", "提醒")):
        if updated.get("intent") in {Intent.UNKNOWN.value, Intent.SUBSCRIPTION_QUERY.value}:
            updated["intent"] = Intent.SUBSCRIPTION_QUERY.value
            updated["confidence"] = None
            updated["target"] = updated.get("target") or prior.get("target")
            if (prior.get("slots") or {}).get("renewal_reminder") is True and "提醒" in text and any(word in text for word in ("同样", "再设置", "重复")):
                updated["slots"] = {**(prior.get("slots") or {}), **(updated.get("slots") or {})}
    card_intents = {Intent.CARD_QUERY.value, Intent.CARD_FREEZE.value, Intent.CARD_UNFREEZE.value,
                    Intent.CARD_REPORT_LOST.value, Intent.CARD_UNREPORT_LOST.value, Intent.CARD_LIMIT.value}
    if prior_intent in card_intents and updated.get("intent") in card_intents and any(word in text for word in ("这张", "那张", "同一张")):
        updated["target"] = updated.get("target") or prior.get("target")
    if any(phrase in text for phrase in ELLIPSIS_SAME_TARGET) and updated.get("target") in (None, "") and prior.get("target"):
        if updated.get("intent") in (None, Intent.UNKNOWN.value):
            updated["intent"] = prior_intent
            updated["confidence"] = None
        updated["target"] = prior.get("target")
    if any(phrase in text for phrase in ELLIPSIS_SAME_PRODUCT) and not updated.get("product_ids") and (prior.get("product_ids") or prior.get("target")):
        candidates = list(prior.get("product_ids") or [])
        if not candidates and prior_intent in {Intent.WEALTH_PURCHASE.value, Intent.WEALTH_REDEEM.value}:
            candidates = [prior["target"]]
        updated["product_ids"] = candidates
        if len(candidates) == 1 and updated.get("intent") in {Intent.WEALTH_PURCHASE.value, Intent.WEALTH_REDEEM.value}:
            updated["target"] = updated.get("target") or candidates[0]
        if updated.get("intent") in (None, Intent.UNKNOWN.value):
            updated["intent"] = prior_intent
            updated["confidence"] = None
    if any(phrase in text for phrase in ELLIPSIS_CANCEL_IT) and updated.get("intent") in (None, Intent.UNKNOWN.value):
        if prior_intent in {Intent.SUBSCRIPTION_QUERY.value, Intent.SUBSCRIPTION_CANCEL.value}:
            updated["intent"] = Intent.SUBSCRIPTION_CANCEL.value
            updated["confidence"] = None
            updated["target"] = prior.get("target")
    return updated


def split_parts(text: str) -> list[str]:
    """Split a compound utterance on explicit sequencing markers."""

    return split_tasks(text)


def split_read_requests(text: str) -> list[dict[str, Any]]:
    """Split one utterance into an ordered list of complete read-only requests.

    Compound requests such as “先看余额，再分析上月账单” are decomposed into
    sub-tasks that the planner turns into one dependency-ordered DAG.  Only
    green (read-only) sub-requests are accepted here: a mutating sub-task would
    need its own approval, so the caller falls back to the single-intent flow.
    """

    parts = split_parts(text)
    if len(parts) < 2 or len(parts) > 8:
        return []
    requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for part in parts:
        parsed = validate_intent_payload(local_parse(part))
        intent = str(parsed.get("intent") or "")
        signature = json.dumps(parsed, sort_keys=True, ensure_ascii=False)
        if intent not in GREEN_INTENTS or signature in seen or needs_clarification(parsed):
            return []
        seen.add(signature)
        requests.append(parsed)
    return requests if len(requests) >= 2 else []


def has_mutating_part(text: str) -> bool:
    """Whether a compound utterance contains a part that changes money/state."""

    parts = split_parts(text)
    if len(parts) < 2:
        return False
    for part in parts:
        try:
            parsed = validate_intent_payload(local_parse(part))
        except Exception:
            continue
        intent = str(parsed.get("intent") or "")
        if intent and intent != Intent.UNKNOWN.value and intent not in GREEN_INTENTS:
            return True
    return False


def build_multi_read_plan(sub_requests: list[dict[str, Any]], plan_id: str, *, sequential: bool = True) -> TaskPlan:
    """Compile several read requests into one dependency-ordered DAG."""

    tasks: list[Task] = []
    previous_last: Optional[str] = None
    for index, parsed in enumerate(sub_requests):
        sub_plan = build_task_plan(parsed, plan_id=f"{plan_id}-{index}")
        id_map = {task.task_id: f"{index}_{task.task_id}" for task in sub_plan.tasks}
        for task in sub_plan.tasks:
            depends = [id_map.get(dep, dep) for dep in task.depends_on]
            if sequential and not depends and previous_last:
                depends = [previous_last]
            tasks.append(
                Task(
                    id_map[task.task_id],
                    task.action,
                    depends,
                    task.risk_level,
                    details={**task.details, "sub_intent": parsed.get("intent")},
                    idempotency_key=f"{plan_id}-{index}:{task.task_id}",
                )
            )
        if sub_plan.tasks:
            previous_last = id_map[sub_plan.tasks[-1].task_id]
    return TaskPlan(plan_id=plan_id, intent=str(sub_requests[0].get("intent") or Intent.UNKNOWN.value), tasks=tasks)


def advance_multi_plan(state: BankingAgentState, sub_requests: list[dict[str, Any]], *, error: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Mark the consumed steps of every sub-request in the shared DAG."""

    plan = plan_from_state(state)
    if plan is None:
        return None
    known = {task.task_id for task in plan.tasks}
    for index, parsed in enumerate(sub_requests):
        # Task ids in the combined plan are prefixed per sub-request, so the
        # consumed/settle ids are derived from that sub-request's own plan.
        sub_plan = build_task_plan(parsed, plan_id=f"multi-{index}")
        _, settle = consumed_task_ids(sub_plan, str(parsed.get("intent") or ""))
        for task in sub_plan.tasks:
            prefixed = f"{index}_{task.task_id}"
            if prefixed not in known:
                continue
            if error and settle and task.task_id == settle:
                PlanExecutor.mark_failed(plan, prefixed, error=error)
            else:
                PlanExecutor.mark_succeeded(plan, prefixed)
    for task in plan.tasks:
        if task.status.value == "PENDING":
            PlanExecutor.keep_pending(plan, task.task_id, note=task.details.get("note") or "由核心任务中心接管")
    return plan.to_dict()


def merge_pending_clarification(parsed: dict[str, Any], state: BankingAgentState, text: str) -> dict[str, Any]:
    """Fold a bare slot answer back into the intent that is waiting for it.

    After the agent asks for a missing slot the user typically replies with
    only that value ("下周六", "500 元", "李四").  Parsing it as a brand-new
    request yields UNKNOWN and silently drops the pending task, so the answer
    is merged into the pending intent instead.  A complete new request of a
    different intent always wins over the pending one.
    """

    prior = state.get("context")

    if not isinstance(prior, dict):

        prior = {}
    pending = prior.get("pending_clarification")
    if not isinstance(pending, dict):
        return parsed
    pending_intent = str(pending.get("intent") or "")
    pending_payload = pending.get("payload")
    if not isinstance(pending_payload, dict):
        pending_payload = {}
    if not pending_intent or pending_intent == Intent.UNKNOWN.value or not pending_payload:
        return parsed
    incoming_intent = str(parsed.get("intent") or Intent.UNKNOWN.value)
    if incoming_intent == Intent.UNKNOWN.value and pending_intent not in {Intent.BILL_QUERY.value, Intent.BILL_ANALYSIS.value}:
        parsed = {**parsed, "date_range": None, "date_scope": "ALL"}
        parsed["slots"] = {key: value for key, value in (parsed.get("slots") or {}).items() if key != "date_range_ambiguous"}
    target_answer = _slot_answer(text, "target", parsed)
    # A model seeing only "CARD-001" or "Apple那个" may classify it as a
    # complete query. The pending question supplies the action for a literal
    # target answer, while a fresh request such as "查询CARD-001" still wins.
    bare_target_answer = (
        "target" in (pending.get("missing") or [])
        and isinstance(target_answer, str)
        and bool(target_answer)
        and re.fullmatch(
            r"\s*(?:是|就|选)?\s*" + re.escape(target_answer)
            + r"\s*(?:那个|这个|那张|这张|那家|这家)?(?:的)?(?:吧)?[。！!，,]?\s*",
            text,
            flags=re.IGNORECASE,
        ) is not None
    )
    if (incoming_intent != Intent.UNKNOWN.value
            and not needs_clarification(parsed) and not bare_target_answer):
        # A complete restatement is authoritative even when it names the same
        # intent.  Otherwise a low-confidence clarification created by an older
        # parser can trap a now-valid request forever (for example repeatedly
        # asking what "理财" means after it became a WEALTH_QUERY entry point).
        return parsed
    merged: dict[str, Any] = dict(pending_payload)
    for key in ("target", "amount", "participants", "product_ids", "scheduled_at", "event_at", "date_range"):
        value = parsed.get(key)
        if value not in (None, "", [], {}):
            merged[key] = value
    if parsed.get("date_scope") not in (None, "ALL"):
        merged["date_scope"] = parsed["date_scope"]
    previous_note = str(pending_payload.get("extra_info") or "")
    # Slot answers supplement the request; they must not erase its memo or purpose.
    merged["extra_info"] = (previous_note + "；补充：" + text).strip("；")[-500:]
    new_slots = parsed.get("slots")
    if isinstance(new_slots, dict) and new_slots:
        merged["slots"] = {**(pending_payload.get("slots") or {}), **new_slots}
    for field in ("amount", "target", "date_range"):
        key = f"{field}_ambiguous"
        if (parsed.get("slots") or {}).get(key):
            merged[field] = None
        elif parsed.get(field) not in (None, "", {}):
            merged["slots"] = {**(merged.get("slots") or {}), key: False}
    if (pending_intent in {Intent.BILL_QUERY.value, Intent.BILL_ANALYSIS.value}
            and not (parsed.get("slots") or {}).get("date_range_ambiguous")
            and re.search(r"今天|今日|本月|这个月|上月|上个月|今年|去年|全部|所有历史", text)):
        merged["date_scope"] = parsed.get("date_scope") or "ALL"
        merged["date_range"] = None
        merged["slots"] = {**(merged.get("slots") or {}), "date_range_ambiguous": False}
    merged["intent"] = pending_intent
    # Fill whatever the question asked for.  A short answer carries no intent
    # keyword, so it is interpreted in the type of the missing slot instead of
    # asking the same question again ("3000" after a card-limit question).
    for slot in (pending.get("missing") or []):
        slot = str(slot)
        if (parsed.get("slots") or {}).get(f"{slot}_ambiguous"):
            continue
        if slot in {"loss_tolerance", "experience", "horizon_years"}:
            slots = merged.get("slots")
            if not isinstance(slots, Mapping):
                slots = {}
            if slots.get(slot) in (None, "", [], {}):
                value = _slot_answer(text, slot, parsed)
                if value is not None:
                    merged["slots"] = {**dict(slots), slot: value}
            continue
        if merged.get(slot) in (None, "", [], {}):
            value = _slot_answer(text, slot, parsed)
            if value is not None:
                merged[slot] = value
                merged["slots"] = {**(merged.get("slots") or {}), f"{slot}_ambiguous": False}
    merged["parse_meta"] = {"source": "rules_context", "resolution": "clarification", "reason_codes": [], "fast_path_eligible": False}
    # A short answer carries no intent confidence of its own; inherit the
    # confidence of the turn that opened the clarification.
    try:
        merged["confidence"] = float(pending["confidence"]) if pending.get("confidence") is not None else None
    except (TypeError, ValueError):
        merged["confidence"] = None
    has_explicit_change = any(
        merged.get(key) != pending_payload.get(key)
        for key in ("target", "amount", "participants", "product_ids", "scheduled_at", "event_at", "slots")
    )
    if has_explicit_change:
        # A vague correction deliberately lowered confidence. Once a later
        # answer actually replaces a grounded business slot, that temporary
        # clarification state must not keep the request stuck indefinitely.
        merged["confidence"] = None
    elif pending.get("revision"):
        # The old approval is cancelled, but "改一下" alone does not authorize
        # simply issuing an identical new approval under a different handle.
        merged["confidence"] = 0.4
    try:
        return validate_intent_payload(merged)
    except Exception:
        return parsed
