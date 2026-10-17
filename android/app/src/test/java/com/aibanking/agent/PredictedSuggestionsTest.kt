package com.aibanking.agent

import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.data.TurnEnvelope
import com.aibanking.agent.ui.chat.cleanSuggestions
import com.aibanking.agent.ui.chat.predictedSuggestions
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PredictedSuggestionsTest {
    @Test
    fun a_completed_turn_shows_no_rule_based_guesses() {
        val suggestions = predictedSuggestions(
            TurnEnvelope(intent = "TRANSFER", result = mapOf("execution_result" to mapOf("type" to "TRANSFER_SUCCESS"))),
        )
        assertTrue(suggestions.isEmpty())
    }

    @Test
    fun examples_offered_by_the_reply_are_shown_at_once() {
        val suggestions = predictedSuggestions(
            TurnEnvelope(intent = "WEALTH_COMPARE", result = mapOf("suggestions" to listOf("比较 LX00118 和 HQ30060"))),
        )
        assertEquals(listOf("比较 LX00118 和 HQ30060"), suggestions)
    }

    @Test
    fun a_pending_reservation_shows_approval_controls_not_a_rollback() {
        val suggestions = predictedSuggestions(
            TurnEnvelope(intent = "SCHEDULED_TRANSFER", approval = ApprovalView(approvalId = "approval-2")),
        )
        assertEquals(listOf("取消这次操作", "修改这次操作", "更换办理对象"), suggestions)
    }

    @Test
    fun approval_only_offers_safe_revision_paths() {
        val suggestions = predictedSuggestions(
            TurnEnvelope(
                intent = "TRANSFER",
                approval = ApprovalView(approvalId = "approval-1"),
                result = mapOf("suggestions" to listOf("申购 LX00118 500 元")),
            ),
        )

        assertEquals(listOf("取消这次操作", "修改这次操作", "更换办理对象"), suggestions)
        assertTrue(suggestions.none { it.contains("确认") || it.contains("执行") })
    }

    @Test
    fun server_suggestions_are_deduplicated() {
        val suggestions = predictedSuggestions(
            TurnEnvelope(
                intent = "WEALTH_RISK_ASSESSMENT",
                result = mapOf("suggestions" to listOf("查看理财产品", "查看理财产品", "查看我的持仓")),
            ),
        )

        assertEquals(listOf("查看理财产品", "查看我的持仓"), suggestions)
    }

    @Test
    fun model_chips_are_trimmed_bounded_and_deduplicated() {
        val chips = cleanSuggestions(listOf(" 查看本月账单 ", "查看本月账单", "好", "这是一条明显超过二十个字上限的推荐问题文字内容示例", "查询最近转账", "看看理财", "查余额"))
        assertEquals(listOf("查看本月账单", "查询最近转账", "看看理财"), chips)
    }
}
