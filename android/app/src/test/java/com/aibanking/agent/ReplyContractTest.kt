package com.aibanking.agent

import com.aibanking.agent.data.TurnEnvelope
import com.aibanking.agent.ui.chat.businessStatusLabel
import com.google.gson.Gson
import org.junit.Assert.*
import org.junit.Test

class ReplyContractTest {
    @Test fun workflow_completion_and_business_failure_remain_distinct() {
        val envelope = Gson().fromJson("""{"status":"COMPLETED","business_status":"FAILED","reply":"查询未完成","response":{"version":1,"status":"FAILED","summary":"查询未完成","sections":[]}}""", TurnEnvelope::class.java)
        assertFalse(envelope.approvalRequired)
        assertEquals("未完成", businessStatusLabel(checkNotNull(envelope.businessStatus)))
        assertEquals("FAILED", envelope.response?.status)
    }

    @Test fun canonical_sections_replace_legacy_receipts_and_keep_questionnaire() {
        val envelope = Gson().fromJson("""{"result":{"execution_result":{"type":"OLD"},"risk_quiz":{"active":false}},"response":{"version":1,"sections":[{"type":"BALANCE","status":"SUCCEEDED","data":{"type":"BALANCE","data":{"available_balance":10}}}]}}""", TurnEnvelope::class.java)
        assertFalse(envelope.displayResult.orEmpty().containsKey("execution_result"))
        assertTrue(envelope.displayResult.orEmpty().containsKey("risk_quiz"))
        assertEquals("BALANCE", ((envelope.displayResult?.get("execution_results") as List<*>).first() as Map<*, *>)["type"])
    }

    @Test fun legacy_receipts_still_render_without_a_response_version() {
        val envelope = TurnEnvelope(result = mapOf("execution_result" to mapOf("type" to "BALANCE")))
        assertEquals(envelope.result, envelope.displayResult)
    }

    @Test fun pending_receipt_exposes_only_read_only_operation_identifier() {
        val envelope = Gson().fromJson("""{"business_status":"PENDING_RECONCILIATION","response":{"version":1,"status":"PENDING_RECONCILIATION","sections":[],"operation_id":"plan-123:settle"}}""", TurnEnvelope::class.java)
        assertEquals("待核对", businessStatusLabel(checkNotNull(envelope.businessStatus)))
        assertEquals("plan-123:settle", envelope.response?.operationId)
    }
}
