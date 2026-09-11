package com.aibanking.agent.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.fragment.app.FragmentActivity
import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.data.DeviceView
import com.aibanking.agent.data.TaskView
import com.aibanking.agent.security.BiometricGate
import com.aibanking.agent.ui.UiTags
import kotlinx.coroutines.delay
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(viewModel: ChatViewModel, activity: FragmentActivity) {
    val state by viewModel.state.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    val listState = rememberLazyListState()

    // Keep the newest bubble in view.  A LazyColumn does not follow appended
    // items on its own, so without this the answer to a question scrolls in
    // below the fold and the user sees nothing happen.
    LaunchedEffect(state.messages.size, state.pending) {
        val itemCount = state.messages.size + if (state.pending != null) 1 else 0
        if (itemCount > 0) {
            listState.animateScrollToItem(itemCount - 1)
        }
    }

    LaunchedEffect(state.error, state.notice) {
        val message = state.error ?: state.notice
        if (message != null) {
            snackbar.showSnackbar(message)
            viewModel.dismissMessages()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("AI 银行助手") },
                actions = {
                    TextButton(
                        onClick = viewModel::openCards,
                        modifier = Modifier.testTag(UiTags.OPEN_CARDS),
                    ) { Text("卡片") }
                    TextButton(
                        onClick = viewModel::openWealth,
                        modifier = Modifier.testTag(UiTags.OPEN_WEALTH),
                    ) { Text("理财") }
                    TextButton(
                        onClick = viewModel::openTasks,
                        modifier = Modifier.testTag(UiTags.OPEN_TASKS),
                    ) { Text("任务") }
                    TextButton(
                        onClick = viewModel::openDevices,
                        modifier = Modifier.testTag(UiTags.OPEN_DEVICES),
                    ) { Text("设备") }
                    TextButton(
                        onClick = viewModel::openContacts,
                        modifier = Modifier.testTag(UiTags.OPEN_CONTACTS),
                    ) { Text("联系人") }
                    TextButton(onClick = viewModel::logout) { Text("退出") }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .imePadding(),
        ) {
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth(),
                contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(state.messages) { message -> MessageBubble(message) }
                state.pending?.let { approval ->
                    item {
                        ApprovalCard(
                            approval = approval,
                            otp = state.otp,
                            busy = state.busy,
                            onOtpChange = viewModel::onOtpChange,
                            onReject = viewModel::reject,
                            onApprove = viewModel::approve,
                            onBiometricApprove = {
                                // Prove presence locally first; the server still
                                // verifies the device signature it receives.
                                BiometricGate.prompt(
                                    activity = activity,
                                    title = "确认高风险操作",
                                    subtitle = approval.summary,
                                    onSuccess = viewModel::approveWithDeviceSignature,
                                    onFailure = { reason -> viewModel.reportError("生物识别未通过：$reason") },
                                )
                            },
                            canPromptBiometric = BiometricGate.canAuthenticate(activity),
                        )
                    }
                }
            }
            if (state.busy) {
                Box(Modifier.fillMaxWidth().padding(8.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
            }
            // 逐题风险测评：固定出现在输入框上端，点选即进下一题。
            state.quiz?.let { quiz ->
                RiskQuizPanel(
                    quiz = quiz,
                    busy = state.busy,
                    onAnswer = viewModel::onQuizAnswer,
                    onGiveUp = viewModel::giveUpQuiz,
                )
            }
            // 快捷输入建议：点击填入输入框（可修改后再发送）。
            if (state.suggestions.isNotEmpty() && state.quiz == null) {
                LazyRow(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    contentPadding = PaddingValues(horizontal = 12.dp),
                ) {
                    items(state.suggestions.size) { index ->
                        SuggestionChip(
                            onClick = { viewModel.onSuggestionClick(state.suggestions[index]) },
                            label = { Text(state.suggestions[index]) },
                        )
                    }
                }
            }
            InputRow(
                value = state.input,
                enabled = state.canSend,
                onValueChange = viewModel::onInputChange,
                onSend = viewModel::send,
            )
        }
    }

    if (state.showDevices) {
        DevicePanel(
            devices = state.devices,
            busy = state.panelBusy,
            currentDeviceId = viewModel.currentDeviceId(),
            onRevoke = viewModel::revokeDevice,
            onDismiss = viewModel::dismissPanels,
        )
    }
    if (state.showTasks) {
        TaskPanel(
            tasks = state.tasks,
            busy = state.panelBusy,
            onCancel = viewModel::cancelTask,
            onDismiss = viewModel::dismissPanels,
        )
    }
    if (state.showContacts) {
        // 通讯录式全屏界面（列表/详情/新增），覆盖聊天页。
        ContactsUi(state, viewModel)
    }
    if (state.showWealth) {
        // 银行风格理财页（产品超市/详情），申购预填回聊天走红色审批流。
        WealthUi(state, viewModel)
    }
    if (state.showCards) {
        // 银行风格银行卡管理页（卡面/余额/操作/添加）。
        CardsUi(state, viewModel)
    }
}

@Composable
private fun MessageBubble(message: ChatMessage) {
    val isUser = message.role == ChatRole.USER
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        Card(
            colors = CardDefaults.cardColors(
                containerColor = if (isUser) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface,
            ),
            modifier = Modifier.fillMaxWidth(0.92f),
        ) {
            Column {
                Text(
                    text = message.text,
                    modifier = Modifier.padding(12.dp),
                    style = MaterialTheme.typography.bodyMedium,
                )
                // Structured facts (balances, category totals, anomaly flags,
                // the task DAG, the birthday memo) render below the prose.
                ResultExtras(message.result)
                message.traceId?.let { traceId ->
                    Text(
                        text = "链路编号 $traceId",
                        modifier = Modifier.padding(start = 12.dp, end = 12.dp, bottom = 10.dp),
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                    )
                }
            }
        }
    }
}

@Composable
private fun ApprovalCard(
    approval: ApprovalView,
    otp: String,
    busy: Boolean,
    canPromptBiometric: Boolean,
    onOtpChange: (String) -> Unit,
    onReject: () -> Unit,
    onApprove: () -> Unit,
    onBiometricApprove: () -> Unit,
) {
    val isRed = approval.mfaRequired
    // The handle really does expire server-side, so the countdown must move:
    // a frozen number invites an approve tap the core is guaranteed to refuse.
    var remainingSeconds by remember(approval.approvalId) { mutableIntStateOf(approval.expiresIn) }
    LaunchedEffect(approval.approvalId) {
        while (remainingSeconds > 0) {
            delay(1_000)
            remainingSeconds -= 1
        }
    }
    Card(
        colors = CardDefaults.cardColors(
            containerColor = if (isRed) Color(0xFFFFDAD6) else Color(0xFFFFE0B2),
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                text = if (isRed) "红色风险：必须完成服务端多因子认证" else "黄色风险：请确认本次操作",
                style = MaterialTheme.typography.titleSmall,
            )
            Text(approval.summary, style = MaterialTheme.typography.bodyMedium)
            Text(
                text = "审批编号 ${approval.approvalId} · " +
                    if (remainingSeconds > 0) "剩余 $remainingSeconds 秒" else "已超时，请重新发起操作",
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
            )
            if (isRed) {
                OutlinedTextField(
                    value = otp,
                    onValueChange = onOtpChange,
                    label = { Text("短信验证码") },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.APPROVAL_OTP),
                )
                if (!canPromptBiometric) {
                    Text(
                        "本设备既未录入生物识别，也未设置锁屏凭据，无法完成红色风险操作。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (isRed) {
                    Button(
                        onClick = onBiometricApprove,
                        enabled = !busy && canPromptBiometric && otp.length >= 6,
                        modifier = Modifier.testTag(UiTags.APPROVAL_APPROVE),
                    ) { Text("用生物识别签名并执行") }
                } else {
                    Button(
                        onClick = onApprove,
                        enabled = !busy,
                        modifier = Modifier.testTag(UiTags.APPROVAL_APPROVE),
                    ) { Text("确认执行") }
                }
                OutlinedButton(
                    onClick = onReject,
                    enabled = !busy,
                    modifier = Modifier.testTag(UiTags.APPROVAL_REJECT),
                ) { Text("取消操作") }
            }
            Text(
                "确认由核心银行服务端校验；客户端无法自行完成资金变更。",
                style = MaterialTheme.typography.labelSmall,
            )
        }
    }
}

/**
 * 逐题风险测评组件：固定出现在输入框上端，一题一屏、点选即进下一题；
 * 答满 8 题自动汇成一条作答消息提交（评分与定档都在服务端）。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun RiskQuizPanel(
    quiz: RiskQuizUi,
    busy: Boolean,
    onAnswer: (QuizQuestionUi, String) -> Unit,
    onGiveUp: () -> Unit,
) {
    val question = quiz.currentQuestion ?: return
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
    ) {
        Column(Modifier.fillMaxWidth().padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "风险测评 ${quiz.answered + 1}/${quiz.questions.size}",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                )
                TextButton(onClick = onGiveUp, enabled = !busy) { Text("放弃") }
            }
            LinearProgressIndicator(
                progress = { quiz.answered.toFloat() / quiz.questions.size },
                modifier = Modifier.fillMaxWidth(),
            )
            Text(question.text, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Bold)
            question.options.forEach { option ->
                Surface(
                    onClick = { onAnswer(question, option.key) },
                    enabled = !busy,
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.surface,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            option.key,
                            style = MaterialTheme.typography.titleSmall,
                            color = MaterialTheme.colorScheme.primary,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.width(24.dp),
                        )
                        Text(option.label, style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
            Text(
                "完成全部 8 题后自动提交，测评结果决定可申购的产品风险等级。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun InputRow(
    value: String,
    enabled: Boolean,
    onValueChange: (String) -> Unit,
    onSend: () -> Unit,
) {    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface)
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            placeholder = { Text("例如：给李四转 300 元") },
            modifier = Modifier.weight(1f).testTag(UiTags.CHAT_INPUT),
            maxLines = 3,
            shape = RoundedCornerShape(12.dp),
        )
        Button(onClick = onSend, enabled = enabled, modifier = Modifier.testTag(UiTags.CHAT_SEND)) { Text("发送") }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DevicePanel(
    devices: List<DeviceView>,
    busy: Boolean,
    currentDeviceId: String?,
    onRevoke: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp)
                .padding(bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("已注册设备", style = MaterialTheme.typography.titleMedium)
            if (busy) {
                CircularProgressIndicator()
            }
            if (!busy && devices.isEmpty()) {
                Text("没有已注册的设备。", style = MaterialTheme.typography.bodyMedium)
            }
            devices.forEach { device ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(device.label ?: "未知设备", style = MaterialTheme.typography.titleSmall)
                                Text(
                                    "${device.deviceId}\n指纹 ${device.fingerprint.take(16)}…",
                                    style = MaterialTheme.typography.labelSmall,
                                    fontFamily = FontFamily.Monospace,
                                )
                                Text(
                                    buildString {
                                        append(if (device.status == "ACTIVE") "已启用" else "已停用")
                                        append(" · ")
                                        append(if (device.pushEnabled) "推送已开启" else "推送未开启")
                                        if (device.createdAt > 0) append(" · 注册于 ${formatEpochSeconds(device.createdAt)}")
                                    },
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                            if (device.status == "ACTIVE") {
                                OutlinedButton(
                                    onClick = { onRevoke(device.deviceId) },
                                    enabled = !busy,
                                    modifier = Modifier.testTag(UiTags.DEVICE_REVOKE),
                                ) { Text("停用") }
                            }
                        }
                        if (device.deviceId == currentDeviceId && device.status == "ACTIVE") {
                            Text(
                                "这是本机：停用后红色风险操作在本机将不可用。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error,
                            )
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TaskPanel(
    tasks: List<TaskView>,
    busy: Boolean,
    onCancel: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp)
                .padding(bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("待办任务", style = MaterialTheme.typography.titleMedium)
            if (busy) {
                CircularProgressIndicator()
            }
            if (!busy && tasks.isEmpty()) {
                Text("当前没有任务。", style = MaterialTheme.typography.bodyMedium)
            }
            tasks.forEach { task ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(task.taskType, style = MaterialTheme.typography.titleSmall)
                                Text(
                                    "${task.status}${task.runAt?.let { " · 到期 $it" } ?: ""}",
                                    style = MaterialTheme.typography.bodySmall,
                                )
                                taskSummary(task)?.let { detail ->
                                    Text(detail, style = MaterialTheme.typography.bodySmall)
                                }
                            }
                            if (task.status in CANCELLABLE_TASK_STATUSES) {
                                OutlinedButton(
                                    onClick = { onCancel(task.taskId) },
                                    enabled = !busy,
                                    modifier = Modifier.testTag(UiTags.TASK_CANCEL),
                                ) { Text("取消") }
                            }
                        }
                    }
                }
            }
            Text(
                "到期需要资金的任务会先暂停并推送提醒，确认后才会执行。",
                style = MaterialTheme.typography.labelSmall,
            )
        }
    }
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

private fun formatEpochSeconds(epochSeconds: Long): String =
    runCatching {
        DateTimeFormatter.ofPattern("yyyy-MM-dd")
            .withZone(ZoneId.systemDefault())
            .format(Instant.ofEpochSecond(epochSeconds))
    }.getOrDefault("")
