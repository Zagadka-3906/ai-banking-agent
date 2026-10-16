package com.aibanking.agent.ui.chat

/** Presentation only. A completed turn is never evidence of a completed debit. */
enum class OutcomeTone { SUCCESS, FAILURE, PENDING, NEUTRAL }

data class OperationOutcome(val title: String, val label: String, val guidance: String, val tone: OutcomeTone)
data class OperationProblem(val message: String, val code: String?, val httpStatus: Int)

internal fun ChatMessage.hasReconciliationTarget(): Boolean =
    businessStatus == "PENDING_RECONCILIATION" &&
        !response?.operationId.isNullOrBlank() && !conversationId.isNullOrBlank()

internal fun outcomeFor(message: ChatMessage): OperationOutcome? {
    if (message.approvalPreview || message.role != ChatRole.ASSISTANT) return null
    return when (message.businessStatus) {
        "SUCCEEDED" -> successfulOutcome(message)
        "FAILED", "BLOCKED" -> OperationOutcome("本次办理未完成", "未完成", "请查看具体原因，返回对话核对或修改请求。已完成步骤不会自动撤销，请勿重复提交整组操作。", OutcomeTone.FAILURE)
        "PARTIAL_SUCCESS" -> OperationOutcome("部分操作已完成", "部分完成", "已完成步骤的回执仍然有效；请核对未完成步骤，不要重复提交整组操作。", OutcomeTone.PENDING)
        "PENDING_RECONCILIATION" -> OperationOutcome("办理结果待核对", "待核对", "尚未取得最终回执。先核对结果，再决定下一步，避免重复提交。", OutcomeTone.PENDING)
        "CANCELLED" -> OperationOutcome("本次操作已取消", "已取消", "请以已完成步骤的回执为准。取消后续步骤不会自动撤销此前已完成的操作。", OutcomeTone.NEUTRAL)
        else -> null
    }
}

private val completedOperationTitles = mapOf(
    "TRANSFER_SUCCESS" to "转账成功", "SCHEDULED_TRANSFER" to "预约已登记",
    "SPLIT_BILL" to "收款请求已创建", "LOCK_SUCCESS" to "资金已预留",
    "WEALTH_PURCHASE" to "申购成功", "WEALTH_REDEEM" to "赎回成功",
    "CARD_APPLY" to "虚拟卡已开立", "CARD_LIMIT" to "卡片日限额已调整",
    "CARD_CHANNEL" to "交易渠道已更新", "UNDO_OPERATION" to "已撤销",
    "CARD_FREEZE" to "卡片已冻结", "CARD_UNFREEZE" to "卡片已解冻",
    "CARD_REPORT_LOST" to "卡片已挂失", "CARD_UNREPORT_LOST" to "挂失已解除",
    "SUBSCRIPTION_CANCEL" to "自动扣费已取消", "SUBSCRIPTION_REMINDER" to "续费提醒处理结果",
    "FRAUD_DISPUTE" to "保护措施已提交", "PASSWORD_CHANGE" to "登录凭证已更新",
)

private val queryTypes = setOf(
    "BALANCE", "BILL_QUERY", "BILL_ANALYSIS", "WEALTH_QUERY", "WEALTH_HOLDINGS",
    "WEALTH_RECOMMEND", "WEALTH_COMPARE", "SUBSCRIPTION_QUERY", "CARD_QUERY",
)

/**
 * Whether a reply is a task with a result page: something was changed, failed or
 * awaits reconciliation. A successful query (balance, bills, the subscription list
 * that needed confirmation first) is answered in the conversation and has none.
 */
internal fun isTaskOutcome(message: ChatMessage): Boolean {
    val outcome = outcomeFor(message) ?: return false
    if (message.businessStatus != "SUCCEEDED") return true
    val types = receiptsOf(message).mapNotNull { it["type"] as? String }
    return !(types.isNotEmpty() && types.all { it in queryTypes }) || outcome.tone != OutcomeTone.SUCCESS
}

private fun receiptsOf(message: ChatMessage): List<Map<*, *>> =
    // Versioned sections are authoritative, even when empty. Never resurrect a
    // legacy receipt from an earlier operation merely to give this page a title.
    if (message.response?.version == 1) {
        message.response.sections.orEmpty().filter { it.status == "SUCCEEDED" }.map { section ->
            section.data + ("type" to section.type)
        }
    } else {
        buildList<Map<*, *>> {
            (message.result?.get("execution_result") as? Map<*, *>)?.let(::add)
            (message.result?.get("execution_results") as? List<*>)?.filterIsInstance<Map<*, *>>()?.let(::addAll)
        }
    }

private fun successfulOutcome(message: ChatMessage): OperationOutcome {
    val receipts: List<Map<*, *>> = if (message.response?.version == 1) {
        message.response.sections.orEmpty().filter { it.status == "SUCCEEDED" }.map { section ->
            section.data + ("type" to section.type)
        }
    } else {
        buildList<Map<*, *>> {
            (message.result?.get("execution_result") as? Map<*, *>)?.let(::add)
            (message.result?.get("execution_results") as? List<*>)?.filterIsInstance<Map<*, *>>()?.let(::addAll)
        }
    }
    val types = receipts.mapNotNull { it["type"] as? String }
    val writes = types.filter { it in completedOperationTitles }
    val reminders = receipts.filter { it["type"] == "SUBSCRIPTION_REMINDER" }
    val noRemindersCreated = reminders.isNotEmpty() && reminders.all {
        val rows = (it["data"] as? Map<*, *>)?.get("data") as? List<*>
        rows != null && rows.isEmpty()
    }
    val onlyEmptyReminders = types.isNotEmpty() && types.all { it == "SUBSCRIPTION_REMINDER" } && noRemindersCreated
    val profileCompleted = message.result?.get("profile_result") is Map<*, *> || "WEALTH_RISK_ASSESSMENT" in types
    val title = when {
        onlyEmptyReminders -> "本次未建立续费提醒"
        writes.size == 1 -> completedOperationTitles.getValue(writes.single())
        writes.isNotEmpty() -> "办理已完成"
        profileCompleted -> "风险测评已完成"
        types.isNotEmpty() && types.all { it in queryTypes } -> "查询已完成"
        else -> "办理已完成"
    }
    val guidance = buildList {
        if ("SPLIT_BILL" in types) add("收款请求已建立，参与人确认付款后才会入账。")
        if ("SCHEDULED_TRANSFER" in types) add("预约已进入待办任务；到期后仍需您确认，当前没有执行预约扣款。")
        if ("LOCK_SUCCESS" in types) {
            add("已预留资金，预留不代表已向商家付款。请在待办任务中核对提醒与订单草稿。")
        }
        if (reminders.isNotEmpty()) add("请核对回执中的提醒任务状态及未建立原因；提醒不会自动扣费或取消订阅。")
        if ("FRAUD_DISPUTE" in types) add("关联卡片已冻结，争议工单仍需人工审核，当前未执行退款。")
        if ("PASSWORD_CHANGE" in types) add("登录密码已更新，原登录会话已失效，请使用新密码重新登录。")
    }.ifEmpty { listOf("以本页回执和当前账户记录为准。") }.joinToString("\n")
    return OperationOutcome(title, if (onlyEmptyReminders) "请求已处理" else "结果已核验", guidance,
        if (onlyEmptyReminders) OutcomeTone.NEUTRAL else OutcomeTone.SUCCESS)
}

internal fun outcomeFor(problem: OperationProblem): OperationOutcome = when {
    problem.code == "MFA_FAILED" -> OperationOutcome("验证未通过", "待重新验证", "请核对短信验证码或设备验证提示，返回原审批卡重新验证。", OutcomeTone.FAILURE)
    problem.code == "DEVICE_VERIFICATION_FAILED" -> OperationOutcome("设备验证未完成", "待重新验证", "请核对设备验证提示，返回原审批卡重新验证。", OutcomeTone.FAILURE)
    problem.code == "CIRCUIT_OPEN" -> OperationOutcome("操作已暂停", "暂时不可办理", "连续验证失败后操作暂停，请按提示稍后再试。", OutcomeTone.FAILURE)
    problem.code == "APPROVAL_ALREADY_RESOLVED" -> OperationOutcome("该审批已处理", "请核对结果", "请返回对话核对已有回执和账户记录，避免重复提交已处理的操作。", OutcomeTone.PENDING)
    problem.httpStatus == 0 || problem.httpStatus == 408 || problem.httpStatus >= 500 ->
        OperationOutcome("暂未取得办理结果", "结果待核对", "请返回对话核对现有回执和账户记录，再决定是否重新发起。", OutcomeTone.PENDING)
    else -> OperationOutcome("本次请求未完成", "未完成", "请查看原因并返回对话。修改请求后仍需按风险等级确认或验证。", OutcomeTone.FAILURE)
}
