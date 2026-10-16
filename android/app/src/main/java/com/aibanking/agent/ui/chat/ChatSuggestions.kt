package com.aibanking.agent.ui.chat

import android.os.Build
import com.aibanking.agent.data.TurnEnvelope

/** Also recognizes preview cards persisted before [ChatMessage.approvalPreview] existed. */
internal fun ChatMessage.isApprovalPreviewMessage(): Boolean =
    approvalPreview || (role == ChatRole.ASSISTANT && text.trimStart().startsWith("您正准备进行："))

/**
 * Follow-ups after a reservation completes. The first phrase is the rollback
 * request; the gateway parses it as UNDO_OPERATION of the same kind (pinned by
 * tests/test_undo_and_card_channel.py), and Core still asks for confirmation.
 */
internal val UNDO_SUGGESTIONS: Map<String, List<String>> = mapOf(
    "SCHEDULED_TRANSFER" to listOf("撤销这笔预约", "查看账户余额", "再转一笔"),
    "SPLIT_BILL" to listOf("撤回这笔 AA 请求", "查看账户余额", "查询最近转账"),
    "LOCK_SUCCESS" to listOf("取消生日锁定", "查看账户余额", "查看本月账单"),
)

/** Derive bounded, actionable next turns from the completed server turn. */
internal fun predictedSuggestions(envelope: TurnEnvelope): List<String> {
    // Approval controls always win. Graph state can retain suggestions from an
    // earlier turn, and those must never displace the safe revision choices.
    if (envelope.approval != null) {
        return listOf("取消这次操作", "修改这次操作", "更换办理对象")
    }

    val serverSuggestions = (envelope.result?.get("suggestions") as? List<*>)
        .orEmpty()
        .mapNotNull { value ->
            value?.toString()?.trim()?.takeIf { it.isNotEmpty() && it.length <= 80 }
        }
        .distinct()
        .take(4)

    val execution = envelope.result?.get("execution_result") as? Map<*, *>
    val executionType = execution?.get("type")?.toString().orEmpty()
    val context = (envelope.intent.orEmpty() + " " + executionType).uppercase()
    // Server-authored choices belong to the just-completed risk assessment.
    // In other flows they may be stale LangGraph state, so the current intent
    // and verified execution result must determine the next actions instead.
    val currentRiskAssessment =
        envelope.result?.get("profile_result") != null || "WEALTH_RISK_ASSESSMENT" in context
    if (currentRiskAssessment && serverSuggestions.isNotEmpty()) return serverSuggestions

    // A reservation that has not moved money can be withdrawn; offer that first,
    // keyed on the verified execution type so a failed attempt never shows it.
    UNDO_SUGGESTIONS[executionType]?.let { return it }

    return when {
        "TRANSFER" in context || "SPLIT_BILL" in context ->
            listOf("查看账户余额", "查询最近转账", "再转一笔")
        "BALANCE" in context ->
            listOf("查看本月账单", "分析本月消费", "给联系人转账")
        "BILL" in context || "TRANSACTION" in context ->
            listOf("分析本月消费", "查找异常交易", "查看账户余额")
        "WEALTH" in context ->
            listOf("查看理财产品", "比较理财产品", "查看我的持仓")
        "CARD" in context ->
            listOf("查看我的卡片", "调整卡片额度", "挂失银行卡")
        "SUBSCRIPTION" in context -> {
            val cancelSuggestion = activeSubscriptionCancelSuggestion(execution)
            if (cancelSuggestion != null) {
                listOf(cancelSuggestion, "查看下次扣费", "分析订阅支出")
            } else {
                listOf("查看我的订阅", "查看下次扣费", "分析订阅支出")
            }
        }
        "CROSS_SCENE" in context || "LOCK_FUND" in context ->
            listOf("查看账户余额", "查看本月账单", "查看我的订阅")
        else -> serverSuggestions.ifEmpty { DEFAULT_PREDICTED_SUGGESTIONS }
    }
}

/** Build a cancellation action only from an active subscription returned by the core. */
private fun activeSubscriptionCancelSuggestion(execution: Map<*, *>?): String? {
    if (execution?.get("type")?.toString() != "SUBSCRIPTION_QUERY") return null
    val response = execution["data"] as? Map<*, *> ?: return null
    val subscriptions = response["data"] as? List<*> ?: return null
    val merchant = subscriptions.asSequence()
        .mapNotNull { it as? Map<*, *> }
        .firstOrNull { it["status"]?.toString() == "ACTIVE" }
        ?.get("merchant")
        ?.toString()
        ?.trim()
        ?.takeIf { it.isNotEmpty() && it.length <= 60 }
        ?: return null
    val subscriptionName = if (merchant.endsWith("订阅")) merchant else "$merchant 订阅"
    return "取消 $subscriptionName"
}
