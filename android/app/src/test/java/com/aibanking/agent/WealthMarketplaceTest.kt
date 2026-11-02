package com.aibanking.agent

import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.chat.*
import org.junit.Assert.*
import org.junit.Test

class WealthMarketplaceTest {
    private val cash = ProductView(productId = "CASH", name = "现金演示", marketGroup = "cash", riskLevel = "R1", holdingDays = 0, minAmount = 1.0, annualizedYield = .0162)
    private val short = ProductView(productId = "SHORT", name = "短债演示", marketGroup = "low_volatility", riskLevel = "R1", holdingDays = 7, minAmount = 100.0)
    private val long = ProductView(productId = "LONG", name = "长债演示", marketGroup = "low_volatility", riskLevel = "R2", holdingDays = 180, minAmount = 1000.0)

    @Test fun group_ranges_and_term_filters_do_not_reclassify_risk_or_include_cash_in_locked_terms() {
        val products = listOf(cash, short, long)
        val low = WealthMarketGroups.first { it.id == "low_volatility" }
        assertEquals(listOf(short), filterWealthProducts(products, low, "1个月内"))
        assertEquals(listOf(long), filterWealthProducts(products, low, "3个月以上"))
        assertTrue(filterWealthProducts(products, low, "1–3个月").isEmpty())
        assertEquals(listOf(long), filterWealthProducts(products, null, "精选", "long"))
        assertEquals("growth", wealthMarketGroup(cash.copy(riskLevel = "R5")).id)
    }

    @Test fun metrics_keep_their_basis_and_missing_data_is_not_a_zero_or_promised_return() {
        assertEquals("1.62%" to "演示年化参考", wealthDisplayMetric(cash))
        assertEquals("-4.20%" to "近1年涨跌幅（演示）", wealthDisplayMetric(cash.copy(riskLevel = "R5", annualReturnPct = -4.2)))
        assertEquals("—" to "业绩数据未提供", wealthDisplayMetric(short))
    }

    private fun result(type: String = "WEALTH_QUERY", code: Int = 200): Map<String, Any> = mapOf(
        "execution_result" to mapOf("type" to type, "data" to mapOf("code" to code,
            "data" to listOf(mapOf("product_id" to "LX00118", "name" to "零星宝", "risk_level" to "R1", "min_amount" to 1.0)))))

    @Test fun rich_catalogue_has_one_intro_but_focused_comparisons_and_mixed_results_keep_all_text() {
        val answer = "零星宝起购1元，风险R1。以下再重复列一遍产品。"
        val text = displayText(answer, result())
        assertFalse(text.contains("起购1元"))
        assertNotNull(wealthReplyProducts(result()))
        assertEquals(answer, displayText(answer, result("WEALTH_COMPARE")))
        assertEquals(answer, displayText(answer, result(code = 503)))
        val mixed = result() + ("execution_results" to listOf(mapOf("type" to "BALANCE", "data" to mapOf("available_balance" to 12))))
        assertNull(wealthReplyProducts(mixed))
        assertEquals(answer, displayText(answer, mixed))
        assertEquals("资料核验失败", displayText("资料核验失败", result(), "FAILED"))
    }

    @Test fun incomplete_or_empty_products_cannot_hide_an_explanation() {
        val sparse = mapOf("execution_result" to mapOf("type" to "WEALTH_QUERY", "data" to mapOf("code" to 200,
            "data" to listOf(mapOf("product_id" to "X", "name" to "缺失资料")))))
        assertNull(wealthReplyProducts(sparse))
        assertEquals("查询失败的说明", displayText("查询失败的说明", sparse))
    }
}
