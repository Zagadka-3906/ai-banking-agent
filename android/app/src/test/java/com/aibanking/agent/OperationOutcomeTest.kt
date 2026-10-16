package com.aibanking.agent

import com.aibanking.agent.data.ReplyResponse
import com.aibanking.agent.data.ReplySection
import com.aibanking.agent.ui.chat.ChatMessage
import com.aibanking.agent.ui.chat.ChatRole
import com.aibanking.agent.ui.chat.OperationProblem
import com.aibanking.agent.ui.chat.OutcomeTone
import com.aibanking.agent.ui.chat.hasReconciliationTarget
import com.aibanking.agent.ui.chat.outcomeFor
import com.aibanking.agent.ui.chat.revealsOutcomeAfterApproval
import org.junit.Assert.*
import org.junit.Test

class OperationOutcomeTest {
    private fun receipt(type: String, data: Map<String, Any> = emptyMap()): Map<String, Any> =
        mapOf("type" to type, "data" to data)

    private fun completed(vararg receipts: Map<String, Any>): ChatMessage = ChatMessage(
        role = ChatRole.ASSISTANT,
        text = "业务回复",
        businessStatus = "SUCCEEDED",
        response = ReplyResponse(version = 1, status = "SUCCEEDED", sections = receipts.map {
            ReplySection(type = it["type"] as String, status = "SUCCEEDED", data = it)
        }),
    )

    @Test fun completion_requires_business_status_not_prose_or_receipt() {
        val message = completed(receipt("TRANSFER_SUCCESS")).copy(text = "转账成功", businessStatus = null)
        assertNull(outcomeFor(message))
        for (status in listOf("NEEDS_INPUT", "WAITING_APPROVAL", "PENDING", "COMPLETED")) {
            assertNull(status, outcomeFor(message.copy(businessStatus = status)))
        }
    }

    @Test fun user_messages_and_approval_previews_never_offer_a_completed_outcome() {
        val message = completed(receipt("TRANSFER_SUCCESS"))
        assertNull(outcomeFor(message.copy(role = ChatRole.USER)))
        assertNull(outcomeFor(message.copy(approvalPreview = true)))
    }

    @Test fun prior_successful_receipts_do_not_override_current_business_state() {
        val message = completed(receipt("TRANSFER_SUCCESS"))
        val expected = mapOf("FAILED" to OutcomeTone.FAILURE, "BLOCKED" to OutcomeTone.FAILURE,
            "PARTIAL_SUCCESS" to OutcomeTone.PENDING, "PENDING_RECONCILIATION" to OutcomeTone.PENDING,
            "CANCELLED" to OutcomeTone.NEUTRAL)
        expected.forEach { (status, tone) ->
            val outcome = checkNotNull(outcomeFor(message.copy(businessStatus = status)))
            assertEquals(status, tone, outcome.tone)
            assertNotEquals("转账成功", outcome.title)
        }
    }

    @Test fun canonical_sections_replace_stale_legacy_receipts_even_when_empty() {
        val result = mapOf("execution_result" to receipt("TRANSFER_SUCCESS"))
        val query = completed(receipt("BALANCE")).copy(result = result)
        assertEquals("查询已完成", outcomeFor(query)?.title)
        assertEquals("办理已完成", outcomeFor(completed().copy(result = result))?.title)
    }

    @Test fun unsupported_response_version_uses_legacy_receipt_contract() {
        val message = completed(receipt("TRANSFER_SUCCESS")).copy(
            response = ReplyResponse(version = 2, sections = listOf(ReplySection(type = "TRANSFER_SUCCESS"))),
            result = mapOf("execution_result" to receipt("CARD_FREEZE")),
        )
        assertEquals("卡片已冻结", outcomeFor(message)?.title)
    }

    @Test fun legacy_multi_receipt_reply_preserves_all_write_semantics() {
        val message = completed().copy(response = null, result = mapOf("execution_results" to listOf(
            receipt("TRANSFER_SUCCESS"), receipt("SCHEDULED_TRANSFER"),
        )))
        val outcome = checkNotNull(outcomeFor(message))
        assertEquals("办理已完成", outcome.title)
        assertTrue(outcome.guidance.contains("当前没有执行预约扣款"))
    }

    @Test fun unknown_result_type_is_not_assumed_to_be_a_read_only_query() {
        assertEquals("办理已完成", outcomeFor(completed(receipt("FUTURE_OPERATION")))?.title)
        assertEquals("查询已完成", outcomeFor(completed(receipt("BALANCE"), receipt("BILL_QUERY")))?.title)
    }

    @Test fun password_change_requires_login_with_the_new_password() {
        val outcome = checkNotNull(outcomeFor(completed(receipt("PASSWORD_CHANGE"))))
        assertEquals("登录凭证已更新", outcome.title)
        assertTrue(outcome.guidance.contains("原登录会话已失效"))
        assertTrue(outcome.guidance.contains("使用新密码重新登录"))
    }

    @Test fun completed_profile_is_recognized_with_or_without_recommendations() {
        for (message in listOf(completed(), completed(receipt("WEALTH_RECOMMEND")))) {
            assertEquals("风险测评已完成", outcomeFor(message.copy(
                result = mapOf("profile_result" to mapOf("grade" to "C2")),
            ))?.title)
        }
        assertEquals("风险测评已完成", outcomeFor(completed(receipt("WEALTH_RISK_ASSESSMENT")))?.title)
    }

    @Test fun compound_workflow_keeps_every_deferred_payment_explanation() {
        val outcome = checkNotNull(outcomeFor(completed(
            receipt("SPLIT_BILL"), receipt("SCHEDULED_TRANSFER"), receipt("LOCK_SUCCESS"),
        )))
        assertEquals("办理已完成", outcome.title)
        assertTrue(outcome.guidance.contains("参与人确认付款后才会入账"))
        assertTrue(outcome.guidance.contains("当前没有执行预约扣款"))
        assertTrue(outcome.guidance.contains("预留不代表已向商家付款"))
        assertFalse(outcome.guidance.contains("鲜花"))
    }

    @Test fun reminder_with_no_tasks_does_not_claim_a_reminder_was_created() {
        val outcome = checkNotNull(outcomeFor(completed(receipt("SUBSCRIPTION_REMINDER", mapOf(
            "code" to 200, "status" to "CREATED", "data" to emptyList<Map<String, Any>>(),
            "skipped" to listOf(mapOf("subscription_id" to "SUB1", "reason" to "INVALID_NEXT_CHARGE")),
        )))))
        assertEquals("本次未建立续费提醒", outcome.title)
        assertEquals(OutcomeTone.NEUTRAL, outcome.tone)
        assertTrue(outcome.guidance.contains("未建立原因"))
    }

    @Test fun reminder_idempotency_may_return_cancelled_or_failed_tasks() {
        for (status in listOf("CANCELLED", "FAILED", "PENDING", "SUCCEEDED")) {
            val outcome = checkNotNull(outcomeFor(completed(receipt("SUBSCRIPTION_REMINDER", mapOf(
                "code" to 200, "status" to "CREATED", "data" to listOf(mapOf("task_id" to "TASK1", "status" to status)),
            )))))
            assertEquals("续费提醒处理结果", outcome.title)
            assertTrue(outcome.guidance.contains("提醒任务状态"))
            assertTrue(outcome.guidance.contains("不会自动扣费或取消订阅"))
        }
    }

    @Test fun fraud_protection_does_not_claim_refund_or_review_completion() {
        val outcome = checkNotNull(outcomeFor(completed(receipt("FRAUD_DISPUTE"))))
        assertEquals("保护措施已提交", outcome.title)
        assertTrue(outcome.guidance.contains("仍需人工审核"))
        assertTrue(outcome.guidance.contains("未执行退款"))
    }

    @Test fun transport_and_server_errors_do_not_prove_that_an_operation_failed() {
        for (status in listOf(0, 408, 500, 502, 503, 504)) {
            val outcome = outcomeFor(OperationProblem("无法取得响应", null, status))
            assertEquals("HTTP $status", OutcomeTone.PENDING, outcome.tone)
            assertTrue(outcome.guidance.contains("核对现有回执和账户记录"))
        }
        assertEquals(OutcomeTone.FAILURE, outcomeFor(OperationProblem("请求参数有误", "INVALID_INPUT", 400)).tone)
    }

    @Test fun already_resolved_approval_requires_checking_existing_results() {
        val outcome = outcomeFor(OperationProblem("审批已处理", "APPROVAL_ALREADY_RESOLVED", 409))
        assertEquals(OutcomeTone.PENDING, outcome.tone)
        assertTrue(outcome.guidance.contains("避免重复提交"))
    }

    @Test fun verification_errors_keep_specific_guidance_without_echoing_sensitive_values() {
        val outcome = outcomeFor(OperationProblem("OTP:123456", "MFA_FAILED", 0))
        assertEquals(OutcomeTone.FAILURE, outcome.tone)
        assertTrue(outcome.guidance.contains("短信验证码或设备验证"))
        assertFalse(outcome.toString().contains("123456"))
        assertEquals("操作已暂停", outcomeFor(OperationProblem("blocked", "CIRCUIT_OPEN", 0)).title)
        assertEquals("设备验证未完成", outcomeFor(OperationProblem("cancelled", "DEVICE_VERIFICATION_FAILED", 400)).title)
    }

    @Test fun reconciliation_requires_pending_status_and_both_nonblank_identifiers() {
        val pending = completed().copy(businessStatus = "PENDING_RECONCILIATION", conversationId = "conversation-1",
            response = ReplyResponse(version = 1, operationId = "operation-1"))
        assertTrue(pending.hasReconciliationTarget())
        assertFalse(pending.copy(businessStatus = "SUCCEEDED").hasReconciliationTarget())
        assertFalse(pending.copy(response = null).hasReconciliationTarget())
        for (missing in listOf(null, "", "  ")) {
            assertFalse(pending.copy(conversationId = missing).hasReconciliationTarget())
            assertFalse(pending.copy(response = ReplyResponse(version = 1, operationId = missing)).hasReconciliationTarget())
        }
    }

    @Test fun an_approved_task_opens_its_result_page_but_an_approved_query_does_not() {
        assertTrue(revealsOutcomeAfterApproval(completed(receipt("TRANSFER_SUCCESS"))))
        assertTrue(revealsOutcomeAfterApproval(completed(receipt("CARD_CHANNEL"))))
        // The subscription list needs confirmation before it is read; it stays in the conversation.
        assertFalse(revealsOutcomeAfterApproval(completed(receipt("SUBSCRIPTION_QUERY"))))
        assertTrue(revealsOutcomeAfterApproval(completed(receipt("TRANSFER_SUCCESS")).copy(businessStatus = "FAILED")))
        assertFalse(revealsOutcomeAfterApproval(completed(receipt("TRANSFER_SUCCESS")).copy(approvalPreview = true)))
    }
}
