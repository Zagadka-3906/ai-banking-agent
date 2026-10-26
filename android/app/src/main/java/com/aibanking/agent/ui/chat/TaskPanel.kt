package com.aibanking.agent.ui.chat

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import com.aibanking.agent.data.TaskView
import com.aibanking.agent.data.SplitRequestView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.components.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun TaskPanel(
    tasks: List<TaskView>,
    splitRequests: List<SplitRequestView>,
    busy: Boolean,
    onCancel: (String) -> Unit,
    onTakeover: (String, String) -> Unit,
    onPaySplit: (SplitRequestView) -> Unit,
    onDeclineSplit: (String) -> Unit,
    onDismiss: () -> Unit,
    error: String? = null,
    onClearError: () -> Unit = {},
) {
    var takeoverId by rememberSaveable { mutableStateOf<String?>(null) }
    var takeoverReason by rememberSaveable { mutableStateOf("") }
    var reasonEdited by rememberSaveable { mutableStateOf(false) }
    val allowHide by rememberUpdatedState(takeoverId == null && !busy)
    val selected = tasks.firstOrNull { it.taskId == takeoverId }
    LaunchedEffect(selected?.status) {
        if (selected?.status == "HUMAN_TAKEOVER") { takeoverId = null; takeoverReason = "" }
    }
    fun back() {
        if (busy) return
        if (takeoverId != null) takeoverId = null else onDismiss()
    }
    // Host callbacks and the focused sheet window must share the same navigation behavior.
    BackHandler(enabled = true) { back() }
    BankBottomSheet(onDismissRequest = ::back,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = false,
            confirmValueChange = { it != SheetValue.Hidden || allowHide }),
    ) {
        // Register against the sheet window's dispatcher, so Back first leaves its form.
        BackHandler(enabled = true) { back() }
        AnimatedContent(targetState = takeoverId != null, modifier = Modifier.testTag("task_panel"), label = "taskPanelPage") { confirming ->
        if (confirming) {
            Column(Modifier.fillMaxWidth().imePadding().verticalScroll(rememberScrollState()).padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Text("请求人工处理", style = MaterialTheme.typography.titleLarge)
                Text("将暂停本任务及关联的待执行步骤。已完成操作不会撤销，已锁定资金不会自动释放。本机演示没有在线客服。",
                    style = MaterialTheme.typography.bodyMedium)
                OutlinedTextField(value = takeoverReason, onValueChange = { reasonEdited = true; takeoverReason = it.take(300); onClearError() },
                    label = { Text("处理原因") }, enabled = !busy, modifier = Modifier.fillMaxWidth().testTag("task_takeover_reason"), maxLines = 4,
                    isError = reasonEdited && takeoverReason.isBlank(),
                    supportingText = if (reasonEdited && takeoverReason.isBlank()) { { BankFormError("请输入处理原因") } } else null)
                BankFormError(error)
                BankPrimaryButton("确认暂停", {
                    val id = takeoverId
                    if (id != null && takeoverReason.isNotBlank()) onTakeover(id, takeoverReason)
                }, enabled = !busy && takeoverReason.isNotBlank() && selected?.status in CANCELLABLE_TASK_STATUSES,
                    loading = busy, modifier = Modifier.fillMaxWidth().testTag("task_takeover_confirm"))
                BankSecondaryButton("返回任务列表", { takeoverId = null }, enabled = !busy,
                    modifier = Modifier.fillMaxWidth().testTag("task_takeover_back"))
            }
        } else {
        LazyColumn(
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            item { BankScreenHeading("待办任务", subtitle = "计划有序，生活从容", eyebrow = "任务安排") }
            if (busy) item { BankLoadingState(message = "正在更新任务…") }
            if (!busy && tasks.isEmpty() && splitRequests.isEmpty()) item { BankEmptyState("当前没有任务。") }
            if (splitRequests.isNotEmpty()) item { Text("AA 收款请求", style = MaterialTheme.typography.titleMedium) }
            items(splitRequests, key = { "split:${it.splitId}" }) { request ->
                BankSectionCard(modifier = Modifier.animateItem()) {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("${request.fromName} 向您收取 ¥${"%.2f".format(request.share)}", style = MaterialTheme.typography.titleMedium)
                        request.target?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                        Text(if (request.status == "PENDING") "等待您决定；不会自动扣款" else "状态：${request.status}", style = MaterialTheme.typography.bodySmall)
                        if (request.status == "PENDING") {
                            BankSecondaryButton("查看并付款", { onPaySplit(request) }, enabled = !busy, modifier = Modifier.fillMaxWidth())
                            BankSecondaryButton("拒绝付款", { onDeclineSplit(request.splitId) }, enabled = !busy, modifier = Modifier.fillMaxWidth())
                        }
                    }
                }
            }
            items(tasks, key = { it.taskId }) { task ->
                BankSectionCard(modifier = Modifier.animateItem()) {
                    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            BankIcon(BankIconKind.Check, Modifier.size(26.dp), tint = MaterialTheme.colorScheme.secondary)
                            Text(taskTypeLabel(task.taskType), style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                            BankStatusChip(
                                taskStatusLabel(task.status),
                                when (task.status) {
                                    "COMPLETED", "SUCCESS", "SUCCEEDED" -> BankSemanticStatus.RiskGreen
                                    "FAILED" -> BankSemanticStatus.RiskRed
                                    "WAITING_USER" -> BankSemanticStatus.RiskYellow
                                    else -> BankSemanticStatus.Neutral
                                },
                            )
                        }
                        taskSummary(task)?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                        Text("任务编号 · ${task.taskId}", style = MaterialTheme.typography.bodySmall)
                        task.dependsOn.forEach { dependency ->
                            val prerequisite = tasks.firstOrNull { it.taskId == dependency }
                            Text("依赖 · ${prerequisite?.let { taskTypeLabel(it.taskType) } ?: dependency}：${prerequisite?.let { taskStatusLabel(it.status) } ?: "待核对"}", style = MaterialTheme.typography.bodySmall)
                        }
                        task.error?.takeIf { it.isNotBlank() }?.let { Text("原因 · $it", style = MaterialTheme.typography.bodySmall) }
                        if (task.status == "HUMAN_TAKEOVER") {
                            SelectionContainer { Text("已暂停，待人工处理。请将任务编号和原因交给演示负责人核对；本应用不会自动恢复执行。", style = MaterialTheme.typography.bodySmall) }
                        }
                        if (task.status == "FAILED" && task.compensation != null) {
                            Text("存在待核对的补偿事项，请核对账本与回执；此处不代表已退款或已释放资金。", style = MaterialTheme.typography.bodySmall)
                        }
                        formatLocalTimestamp(task.runAt)?.let { Text("计划时间 · $it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                        if (task.status in CANCELLABLE_TASK_STATUSES) {
                            BankSecondaryButton(
                                "取消",
                                { onCancel(task.taskId) },
                                enabled = !busy,
                                modifier = Modifier.fillMaxWidth().testTag(UiTags.TASK_CANCEL),
                            )
                            BankSecondaryButton(
                                "请求人工处理",
                                { takeoverId = task.taskId; takeoverReason = ""; reasonEdited = false; onClearError() },
                                enabled = !busy,
                                modifier = Modifier.fillMaxWidth().testTag("task_takeover"),
                            )
                        }
                    }
                }
            }
            item {
                Text(
                    "到期需要资金的任务会先暂停并显示在任务列表，确认后才会执行。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(vertical = 8.dp),
                )
            }
        }
        }
        }
    }
}

private fun taskStatusLabel(status: String): String = when (status) {
    "PENDING" -> "待执行"
    "WAITING_USER" -> "待您确认"
    "RUNNING" -> "执行中"
    "HUMAN_TAKEOVER" -> "转人工处理"
    "COMPLETED", "SUCCESS", "SUCCEEDED" -> "已完成"
    "FAILED" -> "未完成"
    "CANCELLED", "CANCELED" -> "已取消"
    else -> status
}

private fun taskTypeLabel(type: String): String = when (type.uppercase()) {
    "TRANSFER", "SCHEDULED_TRANSFER" -> "预约转账"
    "REMINDER", "BIRTHDAY_REMINDER" -> "日程提醒"
    "PAYMENT", "BILL_PAYMENT" -> "账单支付"
    "SUBSCRIPTION", "SUBSCRIPTION_RENEWAL", "RENEWAL_REMINDER" -> "订阅续费"
    "ORDER_DRAFT" -> "购物计划"
    "FUND_RESERVATION" -> "资金预留"
    else -> type
}

/** Only tasks that have not settled yet may be cancelled from the phone. */
private val CANCELLABLE_TASK_STATUSES = setOf("PENDING", "WAITING_USER", "FAILED")

private fun taskSummary(task: TaskView): String? {
    (task.payload["message"] as? String)?.takeIf { it.isNotBlank() }?.let { return it }
    val subject = (task.payload["merchant"] as? String) ?: (task.payload["item"] as? String)
    val amount = task.payload["amount"]
    val parts = listOfNotNull(subject, amount?.let { "¥$it" })
    return parts.takeIf { it.isNotEmpty() }?.joinToString(" · ")
}
