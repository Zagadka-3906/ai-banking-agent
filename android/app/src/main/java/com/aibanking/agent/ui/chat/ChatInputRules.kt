package com.aibanking.agent.ui.chat

import java.math.BigDecimal

/**
 * 风险档案 → 可购买的最高产品等级，与服务端 `profile_rank` 保持一致。
 *
 * 服务端有两套词汇：老表单产出 CONSERVATIVE/BALANCED/AGGRESSIVE，8 题问卷产出
 * C1–C5。只认前者会把刚做完问卷的用户判成"未测评"——详情页一边显示"激进型"、
 * 一边说"不适合 R1"，购买入口永远不可用。
 */
internal fun profileRank(profile: String?): Int? = when (profile) {
    "CONSERVATIVE", "C1" -> 1
    "BALANCED", "C2" -> 2
    "C3" -> 3
    "AGGRESSIVE", "C4" -> 4
    "C5" -> 5
    else -> null
}

internal fun productRank(riskLevel: String): Int = riskLevel.removePrefix("R").toIntOrNull() ?: 9

/**
 * 申购金额的输入文法：最多一个小数点、小数最多两位、整数部分最多十位。
 *
 * 空串与孤立的 “.” 是输入过程中的中间态（用户先打小数点），匹配得上但不是
 * 金额，由 [parseAmountInput] 判掉。
 */
internal val AMOUNT_INPUT = Regex("""\d{0,10}(\.\d{0,2})?""")

/**
 * 金额文本 → 金额；非法文本返回 null。
 *
 * 全程走 [BigDecimal]，不经过 Double：十进制文本→Double→字符串的往返会改写
 * 用户输入（0.1+0.2 类误差），大额还会变成科学计数法。
 */
internal fun parseAmountInput(text: String): BigDecimal? {
    val trimmed = text.trim()
    if (!AMOUNT_INPUT.matches(trimmed)) return null
    // "." 或 "" 能过文法但不是金额，BigDecimal 会直接抛，交给 runCatching。
    return runCatching { BigDecimal(trimmed) }.getOrNull()
}

/**
 * 理财回复的产品清单以卡片渲染（见 ResultView），气泡文本里保留头行、
 * 风险提示等说明，去掉会重复的“- `代码` …”清单行。
 */
internal fun displayText(reply: String, result: Map<String, Any>?): String {
    var lines = reply.lines()
    val type = (result?.get("execution_result") as? Map<*, *>)?.get("type")?.toString()
    if (type in setOf("WEALTH_QUERY", "WEALTH_RECOMMEND", "WEALTH_COMPARE")) {
        lines = lines.filterNot { it.trimStart().startsWith("- `") }
    }
    // 测评结果的三行说明渲染成彩色结果卡，文本里不再重复。
    if (result?.get("profile_result") != null) {
        lines = lines.filterNot {
            it.startsWith("✅ 风险测评完成") || it.startsWith("特点：") || it.startsWith("匹配理财产品级别：")
        }
    }
    return lines.filter { it.isNotBlank() }.joinToString("\n")
}
