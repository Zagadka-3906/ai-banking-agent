package com.aibanking.agent

import com.aibanking.agent.ui.chat.displayText
import com.aibanking.agent.ui.chat.hasFocusedBalanceAnswer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The reply text introduces; cards carry the details, so nothing is said twice. */
class DisplayTextTest {
    @Test
    fun a_verified_focused_balance_answer_is_preserved_without_an_extra_card() {
        val reply = "当前可用余额为 **¥15,000.00**。"
        val result = mapOf("execution_results" to listOf(mapOf("type" to "BALANCE", "data" to mapOf("available_balance" to 15000.0))),
            "answer_presentation" to mapOf("version" to 1.0, "source" to "VERIFIED_FACTS", "result_type" to "BALANCE"))
        assertTrue(hasFocusedBalanceAnswer(result))
        assertEquals(reply, displayText(reply, result))
        for (marker in listOf(mapOf("version" to 2, "source" to "VERIFIED_FACTS", "result_type" to "BALANCE"),
            mapOf("version" to 1, "source" to "MODEL_TEXT", "result_type" to "BALANCE"),
            mapOf("version" to 1, "source" to "VERIFIED_FACTS", "result_type" to "TRANSFER_SUCCESS"))) {
            assertFalse(hasFocusedBalanceAnswer(result + ("answer_presentation" to marker)))
        }
    }
    @Test
    fun finished_quiz_introduces_the_level_and_products_once() {
        val text = displayText(
            "✅ 风险测评完成……\n- `LX00118` 零星宝",
            mapOf("profile_result" to mapOf("grade" to "C4"), "execution_result" to mapOf("type" to "WEALTH_RECOMMEND", "data" to mapOf("data" to emptyList<Any>()))),
        )
        assertEquals("✅ 风险测评完成，以下是您的风险等级和与之匹配的在售产品。", text)
    }

    @Test
    fun structured_replies_with_sections_are_shortened_too() {
        val sections = listOf(mapOf("type" to "WEALTH_RECOMMEND", "data" to mapOf("data" to emptyList<Any>())))
        val finished = displayText("📈 按风险偏好匹配到 1 款产品：\n- `LX00118` 零星宝", mapOf("profile_result" to mapOf("grade" to "C4"), "execution_results" to sections))
        assertEquals("✅ 风险测评完成，以下是您的风险等级和与之匹配的在售产品。", finished)
        val listed = displayText("📈 为您匹配到 1 款产品：\n- `LX00118` 零星宝", mapOf("execution_results" to sections))
        assertEquals("📈 为您匹配到 1 款产品：\n- `LX00118` 零星宝", listed)
    }

    @Test
    fun a_level_query_keeps_its_own_sentence() {
        val text = displayText("您当前的风险等级为 **C4 进取型**，详情如下：", mapOf("profile_result" to mapOf("grade" to "C4")))
        assertEquals("您当前的风险等级为 **C4 进取型**，详情如下：", text)
    }

    @Test
    fun an_unfinished_quiz_points_to_the_card_instead_of_listing_questions() {
        val text = displayText("已记录 2/8 题，还差第 3、4 题：\n\n3. 题目……", mapOf("risk_quiz" to mapOf("active" to true)))
        assertEquals("已记录 2/8 题，请在下方继续作答。", text)
    }

    @Test
    fun balance_and_transfer_receipts_are_not_repeated_above_their_card() {
        val balance = displayText("查询成功：账户总余额 ¥15,000.00……", mapOf("execution_result" to mapOf("type" to "BALANCE", "data" to mapOf("balance" to 15000.0))))
        assertEquals("已查询账户余额：", balance)
        val holdings = displayText("📊 您持有 1 笔理财：\n- `HLD1` 零星宝", mapOf("execution_result" to mapOf("type" to "WEALTH_HOLDINGS")))
        assertTrue(holdings.startsWith("📊"))
        assertTrue(holdings.contains("HLD1")) // Incomplete card data cannot replace text.
    }
}
