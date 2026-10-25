package com.aibanking.agent.ui.chat

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.components.*
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import com.aibanking.agent.ui.anim.riseIn
import com.aibanking.agent.ui.anim.BankConversationMotion

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun OperationOutcomeScreen(
    outcome: OperationOutcome,
    message: ChatMessage?,
    problem: OperationProblem?,
    canQuery: Boolean,
    onBack: () -> Unit,
    onBalance: () -> Unit,
    onTasks: () -> Unit,
    onReconcile: () -> Unit,
    snackbarHostState: SnackbarHostState = remember { SnackbarHostState() },
) {
    BackHandler(onBack = onBack)
    var showReceipt by rememberSaveable(message?.id, problem?.message) { mutableStateOf(false) }
    val semantic = LocalBankSemanticColors.current
    val accent = when (outcome.tone) {
        OutcomeTone.SUCCESS -> semantic.riskGreenContainer
        OutcomeTone.FAILURE -> semantic.riskRedContainer
        OutcomeTone.PENDING -> semantic.riskYellowContainer
        OutcomeTone.NEUTRAL -> MaterialTheme.colorScheme.primaryContainer
    }
    Scaffold(
        // Receipt text and actions stay in place while the decorative badge animates.
        modifier = Modifier.riseIn(rise = 0.dp, durationMillis = BankConversationMotion.FeedbackMillis),
        topBar = { TopAppBar(title = { Text("办理结果") }, navigationIcon = {
            IconButton(onClick = onBack, modifier = Modifier.testTag(UiTags.OUTCOME_BACK)) {
                BankIcon(BankIconKind.ArrowBack, contentDescription = "返回管家对话")
            }
        }) },
        bottomBar = {
            Surface(color = MaterialTheme.colorScheme.background) {
                // Reserve space for notices so they cannot intercept receipt actions.
                Column(Modifier.fillMaxWidth()) {
                    SnackbarHost(snackbarHostState)
                    BankPrimaryButton("返回管家对话", onBack,
                        modifier = Modifier.fillMaxWidth().navigationBarsPadding().imePadding()
                            .padding(20.dp).testTag(UiTags.OUTCOME_RETURN))
                }
            }
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState())
            .padding(20.dp).testTag(UiTags.OUTCOME_SCREEN),
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(20.dp)) {
            OutcomeBadge(
                tone = outcome.tone,
                container = accent,
                mark = when (outcome.tone) {
                    OutcomeTone.SUCCESS -> semantic.riskGreen
                    OutcomeTone.FAILURE -> semantic.riskRed
                    OutcomeTone.PENDING -> semantic.riskYellow
                    OutcomeTone.NEUTRAL -> MaterialTheme.colorScheme.onPrimaryContainer
                },
            )
            Text(outcome.title, style = MaterialTheme.typography.headlineMedium,
                modifier = Modifier.testTag(UiTags.OUTCOME_TITLE))
            BankStatusChip(outcome.label, when (outcome.tone) { OutcomeTone.SUCCESS -> BankSemanticStatus.RiskGreen
                OutcomeTone.FAILURE -> BankSemanticStatus.RiskRed
                OutcomeTone.PENDING -> BankSemanticStatus.RiskYellow
                OutcomeTone.NEUTRAL -> BankSemanticStatus.Neutral })
            BankSectionCard(modifier = Modifier.fillMaxWidth()) {
                BankSticker(if (outcome.tone == OutcomeTone.FAILURE) "原因与下一步" else "安言 · 办理回执")
                SelectionContainer { AgentMarkdown(message?.text ?: problem?.message.orEmpty()) }
                ReplyDivider()
                Text(outcome.guidance, style = MaterialTheme.typography.bodyMedium)
            }
            message?.response?.workflowPlan?.takeIf { it.steps.isNotEmpty() }?.let { plan ->
                BankSectionCard(modifier = Modifier.fillMaxWidth()) {
                    Text("本次任务进度", style = MaterialTheme.typography.titleMedium)
                    plan.steps.forEach { step ->
                        // A status chip must not squeeze a long step label out of a narrow screen.
                        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text("${step.index}. ${step.label}", style = MaterialTheme.typography.bodySmall)
                            ReplyStatusChip(step.status, businessStatusLabel(step.status))
                        }
                    }
                }
            }
            if (message != null && (!message.result.isNullOrEmpty() || !message.traceId.isNullOrBlank())) {
                BankSecondaryButton(if (showReceipt) "收起回执详情" else "查看回执详情", { showReceipt = !showReceipt },
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.OUTCOME_RECEIPT))
                if (showReceipt) BankSectionCard(modifier = Modifier.fillMaxWidth()) {
                    SelectionContainer {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            ResultExtras(message.result)
                            message.traceId?.takeIf { it.isNotBlank() }?.let {
                                Text("回复编号", style = MaterialTheme.typography.labelMedium)
                                Text(it, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                }
            }
            if (message?.hasReconciliationTarget() == true) {
                message.reconciliationError?.takeIf { it.isNotBlank() }?.let { error ->
                    Text(error, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.testTag(UiTags.OUTCOME_RECONCILIATION_ERROR)
                            .semantics { liveRegion = LiveRegionMode.Polite })
                }
                BankSecondaryButton(if (message.reconciling) "正在核对结果…" else "核对办理结果", onReconcile,
                    enabled = !message.reconciling, modifier = Modifier.fillMaxWidth().testTag(UiTags.OUTCOME_RECONCILE))
            } else if (message?.businessStatus == "PENDING_RECONCILIATION") {
                Text("此回执缺少核对信息，请返回对话查看现有回执和账户记录。", style = MaterialTheme.typography.bodyMedium)
            }
            if (outcome.tone == OutcomeTone.SUCCESS) {
                BankSecondaryButton("查看账户余额", onBalance, enabled = canQuery,
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.OUTCOME_BALANCE))
                BankSecondaryButton("查看待办任务", onTasks,
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.OUTCOME_TASKS))
            }
        }
    }
}
