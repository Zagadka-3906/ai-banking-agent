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
    fun completed_reservations_offer_their_rollback_first() {
        val expected = mapOf(
            "SCHEDULED_TRANSFER" to ("SCHEDULED_TRANSFER" to "撤销这笔预约"),
            "SPLIT_BILL" to ("SPLIT_BILL" to "撤回这笔 AA 请求"),
            "CROSS_SCENE_LOCK" to ("LOCK_SUCCESS" to "取消生日锁定"),
        )
        for ((intent, pair) in expected) {
            val (type, rollback) = pair
            val suggestions = predictedSuggestions(
                TurnEnvelope(intent = intent, result = mapOf("execution_result" to mapOf("type" to type))),
            )
            assertEquals(rollback, suggestions.first())
        }
    }

    @Test
    fun settled_transfer_never_offers_a_rollback() {
        val suggestions = predictedSuggestions(
            TurnEnvelope(intent = "TRANSFER", result = mapOf("execution_result" to mapOf("type" to "TRANSFER_SUCCESS"))),
        )
        assertTrue(suggestions.none { it.contains("撤销") || it.contains("撤回") })
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

    @Test
    fun subscription_query_offers_named_cancel_action_first() {
        val suggestions = predictedSuggestions(
            TurnEnvelope(
                intent = "SUBSCRIPTION_QUERY",
                result = mapOf(
                    "execution_result" to mapOf(
                        "type" to "SUBSCRIPTION_QUERY",
                        "data" to mapOf(
                            "code" to 200,
                            "data" to listOf(
                                mapOf("merchant" to "Apple 订阅", "status" to "ACTIVE"),
                                mapOf("merchant" to "视频会员", "status" to "ACTIVE"),
                            ),
                        ),
                    ),
                ),
            ),
        )

        assertEquals(listOf("取消 Apple 订阅", "查看下次扣费", "分析订阅支出"), suggestions)
    }

    @Test
    fun subscription_query_never_offers_cancel_for_inactive_items() {
        val suggestions = predictedSuggestions(
            TurnEnvelope(
                intent = "SUBSCRIPTION_QUERY",
                result = mapOf(
                    "execution_result" to mapOf(
                        "type" to "SUBSCRIPTION_QUERY",
                        "data" to mapOf(
                            "data" to listOf(
                                mapOf("merchant" to "Apple 订阅", "status" to "CANCELLED"),
                            ),
                        ),
                    ),
                ),
            ),
        )

        assertTrue(suggestions.none { it.startsWith("取消 Apple") })
    }

    @Test
    fun model_chips_are_trimmed_bounded_and_deduplicated() {
        val chips = cleanSuggestions(listOf(" 查看本月账单 ", "查看本月账单", "好", "这是一条明显超过十六个字的推荐问题文字内容", "查询最近转账", "看看理财", "查余额"))
        assertEquals(listOf("查看本月账单", "查询最近转账", "看看理财"), chips)
    }
}
