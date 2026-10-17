package com.aibanking.agent.ui.chat

import com.aibanking.agent.data.TurnEnvelope

/** Also recognizes preview cards persisted before [ChatMessage.approvalPreview] existed. */
internal fun ChatMessage.isApprovalPreviewMessage(): Boolean =
    approvalPreview || (role == ChatRole.ASSISTANT && text.trimStart().startsWith("您正准备进行："))

/** While an approval waits, only these safe revision paths are offered. */
internal val APPROVAL_SUGGESTIONS = listOf("取消这次操作", "修改这次操作", "更换办理对象")

/** Bound chips before they are shown: short, distinct, at most three. */
internal fun cleanSuggestions(predicted: List<String>): List<String> =
    predicted.map { it.trim() }.filter { it.length in 2..20 }.distinct().take(3)

/**
 * Chips shown as soon as a turn completes. There are no rule-based guesses:
 * besides the approval controls, only wording the reply itself offers
 * ("可以说“有哪些理财产品”") appears at once; the model's predictions follow
 * from [ChatViewModel] when they arrive.
 */
internal fun predictedSuggestions(envelope: TurnEnvelope): List<String> {
    // Approval controls always win over anything else the turn carries.
    if (envelope.approval != null) return APPROVAL_SUGGESTIONS
    return replySuggestions(envelope.result)
}

/** Server-authored chips of this turn: examples from the reply or the finished questionnaire. */
internal fun replySuggestions(result: Map<String, Any>?): List<String> =
    cleanSuggestions((result?.get("suggestions") as? List<*>).orEmpty().mapNotNull { it?.toString() })
