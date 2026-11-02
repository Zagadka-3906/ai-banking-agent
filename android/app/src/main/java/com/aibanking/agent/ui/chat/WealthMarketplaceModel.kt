package com.aibanking.agent.ui.chat

import com.aibanking.agent.data.ProductListResponse
import com.aibanking.agent.data.ProductView
import com.google.gson.Gson
import java.util.Locale

internal data class WealthMarketGroup(
    val id: String, val title: String, val description: String,
    val risks: Set<String>, val filters: List<String>, val compact: Boolean = false,
)

internal val WealthMarketGroups = listOf(
    WealthMarketGroup("cash", "活钱管理", "取用灵活，R1低风险为主", setOf("R1"), listOf("精选"), true),
    WealthMarketGroup("low_volatility", "稳健低波", "追求低波动，R1低风险至R2中低风险", setOf("R1", "R2"), listOf("精选", "1个月内", "1–3个月", "3个月以上"), true),
    WealthMarketGroup("steady_growth", "稳健增值", "收益增强有波动，以R2为主", setOf("R2"), listOf("精选", "3个月内", "3–12个月", "12个月以上")),
    WealthMarketGroup("balanced_growth", "稳中求进", "适度配置权益，R3中风险为主", setOf("R3"), listOf("精选", "多资产", "打新策略", "量化策略")),
    WealthMarketGroup("growth", "进取投资", "追求长期增值，R3中风险至R5高风险", setOf("R3", "R4", "R5"), listOf("精选", "R3", "R4", "R5")),
)

internal fun wealthMarketGroup(product: ProductView): WealthMarketGroup {
    return WealthMarketGroups.firstOrNull { it.id == product.marketGroup && product.riskLevel in it.risks }
        ?: WealthMarketGroups.firstOrNull { product.riskLevel in it.risks }
        ?: WealthMarketGroup("unknown", "待核实分类", "产品风险资料未提供", emptySet(), listOf("精选"))
}

internal fun filterWealthProducts(products: List<ProductView>, group: WealthMarketGroup?, filter: String, query: String = ""): List<ProductView> {
    return products.filter { product ->
        val days = product.holdingDays
        (group == null || wealthMarketGroup(product).id == group.id) &&
            (query.isBlank() || listOf(product.name, product.productId, product.strategy.orEmpty(), product.riskLevel)
                .any { it.contains(query.trim(), ignoreCase = true) }) && when (filter) {
                "1个月内" -> days != null && days in 1..30
                "1–3个月" -> days != null && days in 31..90
                "3个月以上" -> days != null && days > 90
                "3个月内" -> days != null && days in 1..90
                "3–12个月" -> days != null && days in 91..365
                "12个月以上" -> days != null && days > 365
                "多资产", "打新策略", "量化策略" -> product.strategy == filter
                "R3", "R4", "R5" -> product.riskLevel == filter
                else -> true
            }
    }
}

internal fun wealthRiskLabel(level: String): String = when (level) {
    "R1" -> "R1 低风险"
    "R2" -> "R2 中低风险"
    "R3" -> "R3 中风险"
    "R4" -> "R4 中高风险"
    "R5" -> "R5 高风险"
    else -> "风险等级未提供"
}

internal fun wealthDisplayMetric(product: ProductView): Pair<String, String> {
    val annualized = product.annualizedYield?.takeIf { it.isFinite() }
    val historic = product.annualReturnPct?.takeIf { it.isFinite() }
    val (number, label) = if (product.riskLevel in setOf("R1", "R2") && annualized != null) {
        annualized * 100 to "演示年化参考"
    } else historic to "近1年涨跌幅（演示）"
    return (number?.let { String.format(Locale.CHINA, "%.2f%%", it) } ?: "—") to
        (if (number == null) "业绩数据未提供" else label)
}

/** A single successful, complete product collection can replace duplicated prose. */
internal fun wealthReplyProducts(result: Map<String, Any?>?): List<ProductView>? {
    val executions = buildList {
        (result?.get("execution_result") as? Map<*, *>)?.let { add(it) }
        (result?.get("execution_results") as? List<*>)?.filterIsInstance<Map<*, *>>()?.let { addAll(it) }
    }.distinct()
    val execution = if (result?.get("profile_result") is Map<*, *> && executions.all {
            it["type"] in setOf("WEALTH_RISK_ASSESSMENT", "WEALTH_RECOMMEND") }) {
        executions.singleOrNull { it["type"] == "WEALTH_RECOMMEND" }
    } else executions.singleOrNull()
    if (execution == null) return null
    if (execution["type"] !in setOf("WEALTH_QUERY", "WEALTH_RECOMMEND")) return null
    val data = execution["data"] as? Map<*, *> ?: return null
    if ((data["code"] as? Number)?.toDouble() != 200.0) return null
    return parseWealthProductRows(data)
}

internal fun parseWealthProductRows(data: Map<*, *>): List<ProductView>? = runCatching {
    val gson = Gson()
    val response = gson.fromJson(gson.toJson(data), ProductListResponse::class.java)
    response.data.takeIf { products -> products.isNotEmpty() && products.all {
        !it.productId.isNullOrBlank() && !it.name.isNullOrBlank() &&
            it.riskLevel in setOf("R1", "R2", "R3", "R4", "R5") && it.minAmount.isFinite() && it.minAmount > 0
    } }
}.getOrNull()

internal fun hasWealthHoldingCards(result: Map<String, Any?>?): Boolean {
    val executions = buildList {
        (result?.get("execution_result") as? Map<*, *>)?.let { add(it) }
        (result?.get("execution_results") as? List<*>)?.filterIsInstance<Map<*, *>>()?.let { addAll(it) }
    }.distinct()
    val execution = executions.singleOrNull()?.takeIf { it["type"] == "WEALTH_HOLDINGS" } ?: return false
    val data = execution["data"] as? Map<*, *> ?: return false
    if ((data["code"] as? Number)?.toDouble() != 200.0) return false
    val rows = data["data"] as? List<*> ?: return false
    return rows.isNotEmpty() && rows.all {
        val row = it as? Map<*, *> ?: return@all false
        val amount = (row["principal"] as? Number)?.toDouble()
        !row["holding_id"].toString().let { id -> id.isBlank() || id == "null" } &&
            !row["product_name"].toString().let { name -> name.isBlank() || name == "null" } && amount != null && amount.isFinite() && amount >= 0
    }
}
