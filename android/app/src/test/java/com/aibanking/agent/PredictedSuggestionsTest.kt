package com.aibanking.agent

import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.data.TurnEnvelope
import com.aibanking.agent.ui.chat.predictedSuggestions
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PredictedSuggestionsTest {
    @Test
    fun transfer_completion_offers_relevant_follow_ups() {
        val suggestions = predictedSuggestions(
            TurnEnvelope(
                intent = "TRANSFER",
                result = mapOf(
                    "execution_result" to mapOf("type" to "TRANSFER_SUCCESS"),
                    "suggestions" to listOf("查看理财产品", "查看我的持仓"),
                ),
            ),
        )

        assertEquals(listOf("查看账户余额", "查询最近转账", "再转一笔"), suggestions)
    }

    @Test
    fun approval_only_offers_safe_revision_paths() {
        val suggestions = predictedSuggestions(
            TurnEnvelope(
                intent = "TRANSFER",
                approval = ApprovalView(approvalId = "approval-1"),
                result = mapOf("suggestions" to listOf("申购 CJ01002 500 元")),
            ),
        )

        assertEquals(listOf("取消这次操作", "修改这次操作", "更换办理对象"), suggestions)
        assertTrue(suggestions.none { it.contains("确认") || it.contains("执行") })
    }

    @Test
    fun server_suggestions_are_deduplicated_and_take_priority() {
        val suggestions = predictedSuggestions(
            TurnEnvelope(
                intent = "WEALTH_RISK_ASSESSMENT",
                result = mapOf("suggestions" to listOf("查看理财产品", "查看理财产品", "查看我的持仓")),
            ),
        )

        assertEquals(listOf("查看理财产品", "查看我的持仓"), suggestions)
    }
}
