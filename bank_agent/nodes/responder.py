"""Graph node 5: verify the Core result and compose the reply, plan status and receipts."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any


from planner import (
    Intent,
    analyze_transactions,
    filter_transactions,
    safe_analysis_text,
)
from bank_agent.state import (
    BankingAgentState,
)
from bank_agent.verification import (
    _amount_matches_expected,
    _core_success,
    _finite_number,
    _response_validation_failure,
    _valid_balance_payload,
    _valid_bill_facts,
    _valid_card_collection,
    _valid_holding_collection,
    _valid_risk_assessment,
    _valid_subscription_collection,
    _valid_text_field,
    _valid_wealth_collection,
)
from bank_agent.rendering import (
    _bill_scope_label,
    _render_cards,
    _render_holdings,
    _render_subscriptions,
    _render_wealth,
)


def responder_node(state: BankingAgentState) -> dict[str, Any]:
    if state.get("final_response"):
        return {"final_response": state.get("final_response")}
    results = state.get("execution_results")
    if isinstance(results, list) and len(results) > 1:
        rendered: list[str] = []
        sanitized: list[dict[str, Any]] = []
        successes = 0
        for item in results:
            index = int(item.get("sub_index", len(sanitized)))
            sub = (state.get("sub_requests") or [{}] * len(results))[index]
            if item.get("business_status") in {"FAILED", "NEEDS_INPUT"}:
                sanitized.append(item)
                rendered.append(str(item.get("error") or "这项查询未完成"))
                continue
            child = {**state, **sub, "execution_result": item, "execution_results": None, "final_response": None}
            validated = responder_node(child)
            if validated.get("business_status") == "FAILED":
                sanitized.append({"type": item.get("type"), "business_status": "FAILED", "error": "查询回执未通过核验"})
            else:
                successes += 1
                sanitized.append({**item, "business_status": "SUCCEEDED"})
            rendered.append(str(validated.get("final_response") or "查询未完成"))
        return {"final_response": "\n\n".join(rendered), "execution_result": None,
                "execution_results": sanitized, "business_status": "SUCCEEDED" if successes == len(results) else "PARTIAL_SUCCESS" if successes else "FAILED"}
    result = state.get("execution_result") or {}
    if not isinstance(result, dict):
        return _response_validation_failure(state, "RESULT_SHAPE_INVALID")
    rtype = result.get("type")
    if not _valid_text_field(rtype, max_length=128):
        return _response_validation_failure(state, "RESULT_TYPE_MISSING")
    if rtype in {Intent.BILL_QUERY.value, Intent.BILL_ANALYSIS.value} and result.get("date_scope") == "CUSTOM":
        try:
            rows = result.get("records")
            if not isinstance(rows, list) or filter_transactions(rows, "CUSTOM", date_range=result.get("date_range")) != rows:
                return _response_validation_failure(state, "BILL_DATE_RANGE_INVALID")
            if state.get("date_scope") == "CUSTOM" and state.get("date_range") != result.get("date_range"):
                return _response_validation_failure(state, "BILL_DATE_RANGE_MISMATCH")
        except (ValueError, TypeError, KeyError):
            return _response_validation_failure(state, "BILL_DATE_RANGE_INVALID")
    envelope = result.get("data")
    if isinstance(envelope, dict) and (
        ("code" in envelope and envelope["code"] != 200)
        or str(envelope.get("status") or "").upper() in {"FAILED", "FAILURE", "ERROR", "DENIED", "REJECTED"}
        or (envelope.get("user_id") is not None and state.get("user_id") is not None and envelope["user_id"] != state["user_id"])
    ):
        return _response_validation_failure(state, "CORE_ENVELOPE_REJECTED")
    if rtype == "BALANCE":
        data = result.get("data", {})
        if not _valid_balance_payload(data):
            return _response_validation_failure(state, "BALANCE_FIELDS_INVALID")
        return {"final_response": f"查询成功：账户总余额 **¥{float(data.get('total_balance', 0)):,.2f}**，锁定资金 **¥{float(data.get('locked_amount', 0)):,.2f}**，可用余额 **¥{float(data.get('available_balance', 0)):,.2f}**。"}
    if rtype in {Intent.BILL_QUERY.value, Intent.BILL_ANALYSIS.value} and result.get("specific_query") == "OVERSEAS_TRANSACTION":
        rows = result.get("records")
        facts = result.get("facts")
        if (
            not isinstance(rows, list)
            or not _valid_bill_facts(facts)
            or any(not isinstance(row, dict) or not _finite_number(row.get("amount")) for row in rows)
        ):
            return _response_validation_failure(state, "SPECIFIC_BILL_RECORDS_INVALID")
        scope_label = _bill_scope_label(result)
        if not rows:
            return {"final_response": f"{scope_label}的已提供账单中未找到匹配的境外交易。请提供流水号、商户或日期，以便继续核对。"}
        reasons_by_id: dict[str, list[str]] = {}
        for anomaly in facts.get("anomalies") or []:
            reasons = anomaly.get("reasons")
            if not isinstance(reasons, list) or any(not _valid_text_field(reason, max_length=120) for reason in reasons):
                return _response_validation_failure(state, "SPECIFIC_BILL_REASONS_INVALID")
            if anomaly.get("tx_id"):
                reasons_by_id[str(anomaly["tx_id"])] = reasons
        lines = [f"{scope_label}找到 {len(rows)} 笔匹配的境外交易："]
        for row in rows:
            for key in ("tx_id", "merchant", "time", "card_id"):
                if row.get(key) is not None and not _valid_text_field(row[key], max_length=200):
                    return _response_validation_failure(state, "SPECIFIC_BILL_DETAILS_INVALID")
            amount = float(row["amount"])
            amount_label = f"退款/冲正 ¥{-amount:,.2f}" if amount < 0 else f"金额 ¥{amount:,.2f}"
            lines.append(
                f"- 商户：{row.get('merchant') or '记录未提供'}；日期：{row.get('time') or '记录未提供'}；"
                f"{amount_label}；卡片：{row.get('card_id') or '记录未提供'}；流水号：{row.get('tx_id') or '记录未提供'}。"
            )
            reasons = reasons_by_id.get(str(row.get("tx_id")), [])
            lines.append("  规则提示：" + ("、".join(reasons) if reasons else "未命中当前异常规则") + "。")
        if len(rows) > 1:
            lines.append("尚不能确定您指的是哪一笔，请提供要核对的流水号。")
        lines.append("以上仅为账单记录和规则提示，不能据此确认盗刷；本次只做查询，未冻结卡片或创建争议工单。")
        return {"final_response": "\n".join(lines)}
    if rtype == Intent.BILL_QUERY.value:
        rows = result.get("records", [])
        if not isinstance(rows, list):
            return _response_validation_failure(state, "BILL_RECORDS_INVALID")
        category = result.get("category")
        scope_label = _bill_scope_label(result)
        if category is not None and (
            not _valid_text_field(category, max_length=120)
            or any(not isinstance(row, dict) or str(row.get("category") or "其他").strip() != category for row in rows)
        ):
            return _response_validation_failure(state, "BILL_CATEGORY_INVALID")
        if not rows:
            return {"final_response": f"{scope_label}暂无{category}类别的交易记录。" if category else "在指定时间范围内暂无交易记录。"}
        if any(not isinstance(row, dict) or not _finite_number(row.get("amount")) for row in rows):
            return _response_validation_failure(state, "BILL_AMOUNT_INVALID")
        total = sum(max(float(row["amount"]), 0.0) for row in rows)
        refunds = sum(-min(float(row["amount"]), 0.0) for row in rows)
        net = total - refunds
        if not all(math.isfinite(value) for value in (total, refunds, net)):
            return _response_validation_failure(state, "BILL_TOTAL_INVALID")
        lines = [f"- {row.get('time', '未知时间')} | {row.get('merchant', '未知商户')} | ¥{float(row.get('amount', 0) or 0):,.2f}" for row in rows]
        category_label = f"{scope_label}{category or ''}：" if category or result.get("date_scope") == "CUSTOM" else ""
        summary = f"📋 {category_label}共 {len(rows)} 笔交易，合计支出 **¥{total:,.2f}**"
        if refunds > 0:
            summary += f"，退款 **¥{refunds:,.2f}**，净支出 **¥{net:,.2f}**"
        return {"final_response": summary + "：\n\n" + "\n".join(lines)}
    if rtype == Intent.BILL_ANALYSIS.value:
        if not isinstance(result.get("records", []), list):
            return _response_validation_failure(state, "BILL_ANALYSIS_RECORDS_INVALID")
        category = result.get("category")
        rows = result.get("records", [])
        scope_label = _bill_scope_label(result)
        if category is not None and (
            not _valid_text_field(category, max_length=120)
            or any(not isinstance(row, dict) or str(row.get("category") or "其他").strip() != category for row in rows)
        ):
            return _response_validation_failure(state, "BILL_CATEGORY_INVALID")
        facts = result.get("facts") or analyze_transactions(
            result.get("records", []), user_id=state.get("user_id"), request_id=state.get("request_id"),
        )
        if not _valid_bill_facts(facts):
            return _response_validation_failure(state, "BILL_FACTS_INVALID")
        if category and not rows:
            return {"final_response": f"{scope_label}暂无{category}类别的交易记录。"}
        try:
            heading = f"{scope_label}{category or ''}消费分析：\n" if category or result.get("date_scope") == "CUSTOM" else ""
            return {"final_response": heading + safe_analysis_text(facts)}
        except (KeyError, TypeError, ValueError, OverflowError):
            return _response_validation_failure(state, "BILL_FACTS_RENDER_INVALID")
    if rtype == "LOCK_SUCCESS":
        data = result.get("data", {})
        required = ("lock_id", "locked_amount", "remaining_available")
        if (
            not isinstance(data, dict)
            or str(data.get("status", "")).upper() != "LOCKED"
            or any(key not in data for key in required)
            or not _valid_text_field(data.get("lock_id"), max_length=128)
            or not _finite_number(data.get("locked_amount"), nonnegative=True)
            or not _finite_number(data.get("remaining_available"), nonnegative=True)
        ):
            return _response_validation_failure(state, "LOCK_RESULT_INVALID")
        if state.get("amount") is not None and float(data.get("locked_amount", 0)) + 0.01 < float(state.get("amount") or 0):
            return _response_validation_failure(state, "LOCK_AMOUNT_MISMATCH")
        reply = f"跨场景任务已建立：当前锁定资金合计 **¥{float(data['locked_amount']):,.2f}**，剩余可用余额 **¥{float(data['remaining_available']):,.2f}**。"
        if _valid_text_field(data.get("reminder_task_id"), max_length=128):
            from datetime import timezone
            try:
                reminder = datetime.fromisoformat(str(data.get("reminder_at") or "").replace("Z", "+00:00"))
                local_zone = timezone(timedelta(hours=8))
                reminder = reminder.replace(tzinfo=local_zone) if reminder.tzinfo is None else reminder.astimezone(local_zone)
                reminder_label = reminder.strftime("%Y-%m-%d %H:%M") + "（北京时间）"
            except ValueError:
                return _response_validation_failure(state, "LOCK_REMINDER_TIME_INVALID")
            reply += f"提醒任务已进入待办队列，计划时间为 {reminder_label}。"
        order_ids = data.get("order_task_ids")
        if isinstance(order_ids, list) and order_ids and all(_valid_text_field(item, max_length=128) for item in order_ids):
            reply += "鲜花和蛋糕仅建立了待确认订单草稿，尚未购买或向商户付款；到期仍需您确认。"
        return {"final_response": reply}
    if rtype == "TRANSFER_SUCCESS":
        data = result.get("data", {})
        required = ("transaction_id", "receiver_name", "transferred_amount", "sender_balance")
        if (
            not isinstance(data, dict)
            or str(data.get("status", "")).upper() != "SUCCESS"
            or any(key not in data for key in required)
            or not _valid_text_field(data.get("transaction_id"), max_length=128)
            or not _valid_text_field(data.get("receiver_name"), max_length=200)
            or not _amount_matches_expected(data.get("transferred_amount"), state.get("amount"))
            or not _finite_number(data.get("sender_balance"), nonnegative=True)
        ):
            return _response_validation_failure(state, "TRANSFER_RESULT_INVALID")
        expected_receiver = state.get("target")
        canonical = (state.get("context") or {}).get("transfer_receipt_payee")
        if isinstance(canonical, dict) and canonical.get("request_id") == state.get("request_id") and state.get("request_id"):
            if (
                canonical.get("requested_target") != state.get("target")
                or not _valid_text_field(canonical.get("target_name"), max_length=200)
                or not _valid_text_field(canonical.get("target_id"), max_length=128)
            ):
                return _response_validation_failure(state, "TRANSFER_PAYEE_CONTEXT_INVALID")
            expected_receiver = canonical["target_name"]
        if expected_receiver is not None and data["receiver_name"] != expected_receiver:
            return _response_validation_failure(state, "TRANSFER_RECIPIENT_MISMATCH")
        return {"final_response": f"✅ 转账结算成功。流水号 `{data.get('transaction_id', '待核心返回')}`，收款人 {data.get('receiver_name', '未知')}，金额 ¥{float(data.get('transferred_amount', 0)):,.2f}，当前余额 ¥{float(data.get('sender_balance', 0)):,.2f}。"}
    if rtype == Intent.WEALTH_RISK_ASSESSMENT.value:
        data = result.get("data") or {}
        if not _valid_risk_assessment(data):
            return _response_validation_failure(state, "RISK_ASSESSMENT_RESULT_INVALID")
        return {"final_response": f"风险评估完成：当前演示风险档案为 **{data['risk_profile']}**。{data['disclaimer']}"}
    if rtype == "SUBSCRIPTION_REMINDER":
        data = result.get("data")
        if not isinstance(data, dict) or data.get("code") != 200 or data.get("status") != "CREATED" or not isinstance(data.get("data"), list):
            return _response_validation_failure(state, "SUBSCRIPTION_REMINDER_INVALID")
        rows = data["data"]
        lines = ["续费提醒安排：" if rows else "本次没有建立续费提醒。"]
        status_labels = {"PENDING": "等待提醒", "RUNNING": "处理中", "SUCCEEDED": "已提醒", "FAILED": "提醒失败", "CANCELLED": "已取消", "WAITING_USER": "等待确认"}
        for row in rows:
            payload = row.get("payload") if isinstance(row, dict) else None
            if (
                not isinstance(payload, dict)
                or row.get("task_type") != "RENEWAL_REMINDER"
                or row.get("status") not in status_labels
                or not _valid_text_field(row.get("task_id"), max_length=160)
                or not _valid_text_field(row.get("run_at"), max_length=80)
                or not _valid_text_field(payload.get("subscription_id"), max_length=128)
                or not _valid_text_field(payload.get("merchant"), max_length=200)
                or not _valid_text_field(payload.get("next_charge"), max_length=80)
                or not _finite_number(payload.get("amount"), nonnegative=True)
            ):
                return _response_validation_failure(state, "SUBSCRIPTION_REMINDER_FIELDS_INVALID")
            try:
                datetime.fromisoformat(row["run_at"])
                datetime.fromisoformat(payload["next_charge"])
            except ValueError:
                return _response_validation_failure(state, "SUBSCRIPTION_REMINDER_DATE_INVALID")
            lines.append(
                f"- {payload['merchant']}：提醒时间 {row['run_at']}；下次扣款 {payload['next_charge']}，"
                f"金额 ¥{float(payload['amount']):,.2f}；{status_labels[row['status']]}（任务编号 `{row['task_id']}`）。"
            )
        for item in data.get("skipped") or []:
            if not isinstance(item, dict):
                return _response_validation_failure(state, "SUBSCRIPTION_REMINDER_SKIPPED_INVALID")
            reason = "扣款日期无效" if item.get("reason") == "INVALID_NEXT_CHARGE" else str(item.get("reason") or "资料不完整")
            lines.append(f"未建立提醒：{item.get('subscription_id') or '订阅'}，{reason}。")
        lines.append("这里只安排提醒，不会自动扣费或取消订阅。")
        return {"final_response": "\n".join(lines)}
    if rtype in {Intent.WEALTH_QUERY.value, Intent.WEALTH_RECOMMEND.value, Intent.WEALTH_COMPARE.value, Intent.WEALTH_HOLDINGS.value, Intent.SUBSCRIPTION_QUERY.value, Intent.CARD_QUERY.value}:
        data = result.get("data") or {}
        if rtype == Intent.WEALTH_HOLDINGS.value:
            valid = _valid_holding_collection(data)
        elif rtype in {Intent.WEALTH_QUERY.value, Intent.WEALTH_RECOMMEND.value, Intent.WEALTH_COMPARE.value}:
            valid = _valid_wealth_collection(data)
        elif rtype == Intent.SUBSCRIPTION_QUERY.value:
            valid = _valid_subscription_collection(data)
        else:
            valid = _valid_card_collection(data)
        if not valid:
            return _response_validation_failure(state, "READ_COLLECTION_INVALID")
        # Render a readable answer from the verified fields.  The raw payload is
        # deliberately not shown to a customer; every value below comes from the
        # validated core response (no invented numbers).
        if rtype == Intent.WEALTH_HOLDINGS.value:
            # New turns clear execution_result; carry only verified product
            # identities into context so singular references survive that reset.
            product_ids = list(dict.fromkeys(row["product_id"] for row in data["data"] if row["principal"] > 0))
            return {"final_response": _render_holdings(data),
                    "context": {**(state.get("context") or {}), "verified_product_ids": product_ids}}
        if rtype in {Intent.WEALTH_QUERY.value, Intent.WEALTH_RECOMMEND.value, Intent.WEALTH_COMPARE.value}:
            return {"final_response": _render_wealth(data, rtype)}
        if rtype == Intent.SUBSCRIPTION_QUERY.value:
            return {"final_response": _render_subscriptions(data)}
        return {"final_response": _render_cards(data)}
    if rtype == Intent.WEALTH_PURCHASE.value:
        envelope = result.get("data")
        data = envelope.get("data") if isinstance(envelope, dict) else None
        holding = data.get("holding", {}) if isinstance(data, dict) else {}
        product = data.get("product") if isinstance(data, dict) else None
        if (
            not _core_success(envelope, nested_status="PURCHASED", required=("holding", "product"))
            or not isinstance(data, dict)
            or not isinstance(holding, dict)
            or not _valid_text_field(holding.get("holding_id"), max_length=128)
            or not _amount_matches_expected(holding.get("principal"), state.get("amount"))
            or not isinstance(product, dict)
            or not _valid_text_field(product.get("name"), max_length=200)
            or not _valid_text_field(product.get("product_id"), max_length=64)
            or holding.get("product_id") != product.get("product_id")
            or (state.get("target") is not None and product.get("product_id") != state["target"])
        ):
            return _response_validation_failure(state, "WEALTH_PURCHASE_RESULT_INVALID")
        return {"final_response": f"理财申购已由核心确认：{product.get('name')}，金额 ¥{float(holding.get('principal')):,.2f}，持仓编号 `{holding.get('holding_id')}`。以上为沙箱结果，不构成投资建议。"}
    if rtype == Intent.WEALTH_REDEEM.value:
        envelope = result.get("data")
        data = envelope.get("data") if isinstance(envelope, dict) else None
        if (
            not _core_success(envelope, nested_status="REDEEMED", required=("redeemed_amount", "product"))
            or not isinstance(data, dict)
            or not _amount_matches_expected(data.get("redeemed_amount"), state.get("amount"))
            or not isinstance(data.get("product"), dict)
            or not _valid_text_field(data["product"].get("product_id"), max_length=64)
            or (state.get("target") is not None and data["product"].get("product_id") != state["target"])
        ):
            return _response_validation_failure(state, "WEALTH_REDEEM_RESULT_INVALID")
        return {"final_response": f"理财赎回已由核心确认，到账金额 ¥{float(data.get('redeemed_amount')):,.2f}。以上为沙箱结果。"}
    expected_nested_status = {
        Intent.CARD_APPLY.value: "ISSUED",
        Intent.CARD_LIMIT.value: "LIMIT_UPDATED",
        Intent.CARD_FREEZE.value: "FROZEN",
        Intent.CARD_UNFREEZE.value: "ACTIVE",
        Intent.CARD_REPORT_LOST.value: "LOST",
        Intent.CARD_UNREPORT_LOST.value: "ACTIVE",
        Intent.SUBSCRIPTION_CANCEL.value: "CANCELLED",
        Intent.SCHEDULED_TRANSFER.value: "SCHEDULED",
        # Creating an AA request does not move money. Participants pay later.
        Intent.SPLIT_BILL.value: ("PENDING", "PARTIAL", "SETTLED"),
        Intent.PASSWORD_CHANGE.value: "PASSWORD_CHANGED",
        Intent.FRAUD_DISPUTE.value: "CARD_PROTECTED",
    }.get(rtype)
    if rtype:
        if expected_nested_status is None:
            return _response_validation_failure(state, "UNSUPPORTED_RESULT_TYPE")
        envelope = result.get("data")
        if not _core_success(envelope, nested_status=expected_nested_status):
            return _response_validation_failure(state, "GENERIC_RESULT_STATUS_INVALID")
        if expected_nested_status is not None:
            payload = envelope.get("data")
            if not isinstance(payload, dict):
                return _response_validation_failure(state, "GENERIC_RESULT_INVALID")
            required_by_type = {
                Intent.CARD_APPLY.value: ("card",),
                Intent.CARD_LIMIT.value: ("card",),
                Intent.CARD_FREEZE.value: ("card",),
                Intent.CARD_UNFREEZE.value: ("card",),
                Intent.CARD_REPORT_LOST.value: ("card",),
                Intent.CARD_UNREPORT_LOST.value: ("card",),
                Intent.SUBSCRIPTION_CANCEL.value: ("subscription",),
                Intent.SCHEDULED_TRANSFER.value: ("task_id", "schedule_id", "scheduled_at"),
                Intent.SPLIT_BILL.value: ("task_id", "split_id", "participants", "per_person"),
                Intent.PASSWORD_CHANGE.value: ("credential_version",),
                Intent.FRAUD_DISPUTE.value: ("card", "dispute_case", "transaction"),
            }
            if any(key not in payload for key in required_by_type.get(rtype, ())):
                return _response_validation_failure(state, "GENERIC_RESULT_FIELDS_INVALID")
            if rtype.startswith("CARD_") or rtype == Intent.FRAUD_DISPUTE.value:
                card = payload.get("card")
                if not isinstance(card, dict) or not _valid_text_field(card.get("card_id"), max_length=128):
                    return _response_validation_failure(state, "CARD_RESULT_INVALID")
                expected_card_status = {
                    Intent.CARD_APPLY.value: "ACTIVE", Intent.CARD_FREEZE.value: "FROZEN",
                    Intent.CARD_UNFREEZE.value: "ACTIVE", Intent.CARD_REPORT_LOST.value: "LOST",
                    Intent.CARD_UNREPORT_LOST.value: "ACTIVE", Intent.FRAUD_DISPUTE.value: "FROZEN",
                }.get(rtype)
                if expected_card_status and card.get("status") != expected_card_status:
                    return _response_validation_failure(state, "CARD_STATUS_MISMATCH")
                if str(state.get("target") or "").upper().startswith("CARD-") and card["card_id"] != state["target"]:
                    return _response_validation_failure(state, "CARD_TARGET_MISMATCH")
                if rtype == Intent.CARD_LIMIT.value and not _amount_matches_expected(card.get("daily_limit"), state.get("amount")):
                    return _response_validation_failure(state, "CARD_LIMIT_RESULT_INVALID")
                if rtype == Intent.FRAUD_DISPUTE.value:
                    case, transaction = payload.get("dispute_case"), payload.get("transaction")
                    if (
                        not isinstance(case, dict) or not isinstance(transaction, dict)
                        or not _valid_text_field(case.get("case_id"), max_length=128)
                        or not _valid_text_field(transaction.get("tx_id"), max_length=128)
                        or case.get("status") not in {"OPEN", "PENDING_REVIEW"}
                        or case.get("protection_status") != "CARD_PROTECTED"
                        or case.get("owner_id") != state.get("user_id")
                        or case.get("transaction_id") != transaction.get("tx_id")
                        or case.get("card_id") != card["card_id"]
                        or transaction.get("card_id") != card["card_id"]
                        or ((state.get("transaction") or {}).get("tx_id") is not None
                            and state["transaction"]["tx_id"] != transaction["tx_id"])
                    ):
                        return _response_validation_failure(state, "DISPUTE_RESULT_INVALID")
            elif rtype == Intent.SUBSCRIPTION_CANCEL.value:
                subscription = payload.get("subscription")
                if (not isinstance(subscription, dict)
                        or not _valid_text_field(subscription.get("subscription_id"), max_length=128)
                        or not _valid_text_field(subscription.get("merchant"), max_length=200)):
                    return _response_validation_failure(state, "SUBSCRIPTION_RESULT_INVALID")
                if subscription.get("status") != "CANCELLED":
                    return _response_validation_failure(state, "SUBSCRIPTION_STATUS_MISMATCH")
                target = str(state.get("target") or "").casefold()
                if target and target != subscription["subscription_id"].casefold() and target not in subscription["merchant"].casefold():
                    return _response_validation_failure(state, "SUBSCRIPTION_TARGET_MISMATCH")
            elif rtype == Intent.SCHEDULED_TRANSFER.value:
                if not all(_valid_text_field(payload.get(key), max_length=160) for key in ("task_id", "schedule_id", "scheduled_at")):
                    return _response_validation_failure(state, "SCHEDULE_RESULT_INVALID")
                try:
                    actual_time = datetime.fromisoformat(payload["scheduled_at"].replace("Z", "+00:00"))
                    if state.get("scheduled_at"):
                        expected_time = datetime.fromisoformat(str(state["scheduled_at"]).replace("Z", "+00:00"))
                        if actual_time != expected_time:
                            return _response_validation_failure(state, "SCHEDULE_TIME_MISMATCH")
                except ValueError:
                    return _response_validation_failure(state, "SCHEDULE_TIME_INVALID")
                if not _amount_matches_expected(payload.get("amount"), state.get("amount")) or (state.get("target") is not None and payload.get("target") != state["target"]):
                    return _response_validation_failure(state, "SCHEDULE_DETAILS_MISMATCH")
            elif rtype == Intent.SPLIT_BILL.value:
                if not _valid_text_field(payload.get("task_id"), max_length=160) or not _valid_text_field(payload.get("split_id"), max_length=160):
                    return _response_validation_failure(state, "SPLIT_RESULT_INVALID")
                if not isinstance(payload.get("participants"), list) or not _finite_number(payload.get("per_person"), nonnegative=True):
                    return _response_validation_failure(state, "SPLIT_RESULT_INVALID")
                if not all(_finite_number(payload.get(key), nonnegative=True) for key in ("amount", "collected_amount", "outstanding_amount", "initiator_share")):
                    return _response_validation_failure(state, "SPLIT_TOTALS_INVALID")
                amount, collected, outstanding, own_share = (float(payload[key]) for key in ("amount", "collected_amount", "outstanding_amount", "initiator_share"))
                if (not _amount_matches_expected(amount, state.get("amount"))
                        or abs(collected + outstanding + own_share - amount) > 0.01
                        or abs(float(payload["per_person"]) * len(payload["participants"]) + own_share - amount) > 0.01
                        or (state.get("participants") is not None and payload["participants"] != state["participants"])
                        or (payload["status"] == "SETTLED" and outstanding > 0.01)
                        or (payload["status"] == "PARTIAL" and outstanding <= 0)):
                    return _response_validation_failure(state, "SPLIT_FACTS_MISMATCH")
        return {"final_response": _completion_label(state, rtype)}
    return _response_validation_failure(state, "RESULT_TYPE_MISSING")


def _completion_label(state: BankingAgentState, rtype: str) -> str:
    """Business-language confirmation, never an internal intent code."""

    payload = (state.get("execution_result") or {}).get("data") or {}
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    target = state.get("target")
    card = inner.get("card") if isinstance(inner.get("card"), dict) else {}
    card_id = card.get("card_id")
    scheduled_label = inner.get("scheduled_at") or state.get("scheduled_at") or "约定时间"
    if rtype == Intent.SCHEDULED_TRANSFER.value:
        try:
            scheduled = datetime.fromisoformat(str(scheduled_label).replace("Z", "+00:00"))
            local_zone = timezone(timedelta(hours=8))
            scheduled = scheduled.replace(tzinfo=local_zone) if scheduled.tzinfo is None else scheduled.astimezone(local_zone)
            scheduled_label = scheduled.strftime("%Y-%m-%d %H:%M") + "（北京时间）"
        except ValueError:
            pass
    labels = {
        Intent.CARD_APPLY.value: f"✅ 虚拟卡已开立（{card_id}）。" if card_id else "✅ 虚拟卡已开立。",
        Intent.CARD_FREEZE.value: "✅ 卡片交易已冻结。",
        Intent.FRAUD_DISPUTE.value: "✅ 已冻结关联卡片并创建争议工单，案件将由人工审核。未执行退款。",
        Intent.CARD_UNFREEZE.value: "✅ 卡片交易已解冻。",
        Intent.CARD_REPORT_LOST.value: "✅ 卡片已挂失，后续交易将被拒绝。",
        Intent.CARD_UNREPORT_LOST.value: "✅ 已解除挂失，卡片恢复可用。",
        Intent.SUBSCRIPTION_CANCEL.value: f"✅ 已取消 {(inner.get('subscription') or {}).get('merchant') or target or '该'} 的自动扣费。",
        Intent.SCHEDULED_TRANSFER.value: f"✅ 已登记预约转账，预约时间为 {scheduled_label}，到期后仍需您确认才会转出资金。",
        Intent.PASSWORD_CHANGE.value: "✅ 登录凭证版本已轮换（演示不保存明文密码）。",
    }
    if rtype == Intent.CARD_LIMIT.value:
        limit = card.get("daily_limit")
        return f"✅ 卡片日限额已调整为 ¥{float(limit):,.2f}。" if _finite_number(limit, nonnegative=True) else "✅ 卡片日限额已调整。"
    if rtype == Intent.SPLIT_BILL.value:
        split = inner or payload
        share = split.get("per_person")
        own_share = split.get("initiator_share")
        if _finite_number(share, nonnegative=True) and _finite_number(own_share, nonnegative=True):
            return f"✅ AA 收款请求已发送：参与人人均 ¥{float(share):,.2f}，您承担 ¥{float(own_share):,.2f}。当前尚未收款，须由各参与人登录并分别确认付款。"
        return "✅ AA 收款请求已发送；当前尚未收款。"
    return labels.get(rtype, "已完成本次操作。")
