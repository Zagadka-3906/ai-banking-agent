package com.aibanking.agent.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.fragment.app.FragmentActivity
import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.security.BiometricGate
import com.aibanking.agent.ui.UiTags

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
                    Text("已登录：${state.principal}", style = MaterialTheme.typography.labelMedium)
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
            InputRow(
                value = state.input,
                enabled = state.canSend,
                onValueChange = viewModel::onInputChange,
                onSend = viewModel::send,
            )
        }
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
                text = "审批编号 ${approval.approvalId} · 剩余 ${approval.expiresIn} 秒",
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

@Composable
private fun InputRow(
    value: String,
    enabled: Boolean,
    onValueChange: (String) -> Unit,
    onSend: () -> Unit,
) {
    Row(
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
