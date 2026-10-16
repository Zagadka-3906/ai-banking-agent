package com.aibanking.agent.ui.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.components.BankGradientMark
import com.aibanking.agent.ui.components.BankIcon
import com.aibanking.agent.ui.components.BankIconKind
import com.aibanking.agent.ui.components.BankSticker
import com.aibanking.agent.ui.components.BankTextButton
import com.aibanking.agent.ui.components.BankSecondaryButton
import com.aibanking.agent.ui.components.BankSemanticStatus
import com.aibanking.agent.ui.components.BankStatusChip
import com.aibanking.agent.ui.theme.BankPalette
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.riseIn

/** A readable reply with financial facts kept separate from support metadata. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun AgentReplyCard(message: ChatMessage, modifier: Modifier = Modifier, onReconcile: () -> Unit = {}, onOutcome: () -> Unit = {}) {
    var showDetails by rememberSaveable(message.id) { mutableStateOf(false) }
    val colors = MaterialTheme.colorScheme
    // Assistant turns sit directly on the canvas, led by the gradient brand mark.
    Row(
        modifier = modifier.fillMaxWidth().padding(vertical = 6.dp).testTag("reply_card_${message.id}"),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        BankGradientMark(Modifier.padding(top = 2.dp).size(26.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            val risk = when {
                message.approvalPreview -> "待您确认" to BankSemanticStatus.RiskYellow
                else -> when (message.riskLevel?.uppercase()) {
                    "GREEN" -> "绿 · 只读" to BankSemanticStatus.RiskGreen
                    "YELLOW" -> "黄 · 确认操作" to BankSemanticStatus.RiskYellow
                    "RED" -> "红 · 强验证" to BankSemanticStatus.RiskRed
                    else -> null
                }
            }
            if (risk != null || message.businessStatus != null) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    risk?.let { (label, status) -> BankStatusChip(label, status, Modifier.riseIn(delayMillis = 200, rise = 0.dp, fromScale = 0.8f, durationMillis = 240)) }
                    message.businessStatus?.let { ReplyStatusChip(it, businessStatusLabel(it)) }
                }
            }
            SelectionContainer {
                Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    AgentMarkdown(message.text)
                    ResultExtras(message.result)
                    message.response?.workflowPlan?.let { plan ->
                        ReplyPanel {
                            Text("任务进度 · ${plan.currentStep}/${plan.steps.size}", style = MaterialTheme.typography.titleSmall)
                            plan.steps.forEach { step ->
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                                    Text("${step.index}. ${step.label}", Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                                    ReplyStatusChip(step.status, businessStatusLabel(step.status))
                                }
                            }
                        }
                    }
                    if (message.businessStatus == "PENDING_RECONCILIATION") {
                        message.reconciliationError?.let { Text(it, color = colors.error, style = MaterialTheme.typography.bodySmall) }
                        if (!message.reconciling) Text("暂未取得回执，可再次核对。", style = MaterialTheme.typography.bodySmall,
                            color = colors.onSurfaceVariant)
                        if (message.hasReconciliationTarget()) {
                            BankSecondaryButton(if (message.reconciling) "正在核对结果…" else "核对结果", onReconcile,
                                enabled = !message.reconciling, modifier = Modifier.fillMaxWidth().testTag("reconcile_${message.id}"))
                        } else {
                            Text("缺少核对编号，请查看现有回执和账户记录，勿重复办理。", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    if (message.businessStatus == null && !message.stages.isNullOrEmpty()) WorkflowProgress(message.stages)
                }
            }
            // Queries are answered here; only tasks get a result page.
            outcomeFor(message)?.takeIf { isTaskOutcome(message) }?.let { outcome ->
                BankSecondaryButton(when (outcome.tone) {
                    OutcomeTone.SUCCESS -> "查看办理结果"
                    OutcomeTone.FAILURE -> "查看原因与下一步"
                    else -> "查看操作状态"
                }, onOutcome, modifier = Modifier.fillMaxWidth().testTag(UiTags.outcomeEntry(message.id)))
            }
            message.traceId?.takeIf { it.isNotBlank() }?.let { traceId ->
                Column {
                    ReplyDivider()
                    BankTextButton(
                        onClick = { showDetails = !showDetails },
                        modifier = Modifier.testTag("reply_details_${message.id}"),
                        contentPadding = PaddingValues(horizontal = 0.dp, vertical = 8.dp),
                        colors = ButtonDefaults.textButtonColors(contentColor = colors.onSurfaceVariant),
                    ) {
                        Text(if (showDetails) "收起详情" else "回复详情", style = MaterialTheme.typography.labelMedium)
                        Spacer(Modifier.width(6.dp))
                        BankIcon(if (showDetails) BankIconKind.Close else BankIconKind.More, Modifier.size(16.dp))
                    }
                    AnimatedVisibility(
                        visible = showDetails,
                        enter = fadeIn() + expandVertically(),
                        exit = fadeOut() + shrinkVertically(),
                    ) {
                        ReplyPanel {
                            message.stages?.takeIf { it.isNotEmpty() }?.let { WorkflowProgress(it) }
                            Text("回复编号", style = MaterialTheme.typography.labelMedium, color = colors.onSurfaceVariant)
                            SelectionContainer {
                                Text(traceId, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace,
                                    fontWeight = FontWeight.Medium, color = colors.onSurfaceVariant)
                            }
                        }
                    }
                }
            }
        }
    }
}

internal fun businessStatusLabel(status: String): String = when (status) {
    "SUCCEEDED" -> "已核验"
    "FAILED" -> "未完成"
    "BLOCKED" -> "未通过核验"
    "NEEDS_INPUT" -> "待补充"
    "WAITING_APPROVAL" -> "待确认"
    "PENDING_RECONCILIATION" -> "待核对"
    "PARTIAL_SUCCESS" -> "部分完成"
    "CANCELLED" -> "已取消"
    else -> "待办理"
}
