package com.aibanking.agent.ui.chat

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.EnterTransition
import androidx.compose.animation.ExitTransition
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ExitToApp
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.AccountBox
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.SuggestionChipDefaults
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.fragment.app.FragmentActivity
import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.data.DeviceView
import com.aibanking.agent.data.TaskView
import com.aibanking.agent.security.BiometricGate
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.PredictiveBackPreview
import com.aibanking.agent.ui.anim.cubeFlip
import com.aibanking.agent.ui.anim.verificationFrost
import kotlinx.coroutines.delay
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** 聊天之外的三个全屏覆盖页；聊天页是层级最浅的主页。 */
private enum class AppPage { CHAT, CARDS, WEALTH, CONTACTS }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(viewModel: ChatViewModel, activity: FragmentActivity) {
    val state by viewModel.state.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    val listState = rememberLazyListState()

    // 页面状态映射
    val page = when {
        state.showCards -> AppPage.CARDS
        state.showWealth -> AppPage.WEALTH
        state.showContacts -> AppPage.CONTACTS
        else -> AppPage.CHAT
    }

    // 主骨架：屏幕底部常驻现代化 NavigationBar
    Scaffold(
        bottomBar = {
            BankBottomNavigation(
                currentPage = page,
                onSelectPage = { targetPage ->
                    when (targetPage) {
                        AppPage.CHAT -> viewModel.dismissPanels()
                        AppPage.CARDS -> viewModel.openCards()
                        AppPage.WEALTH -> viewModel.openWealth()
                        AppPage.CONTACTS -> viewModel.openContacts()
                    }
                },
            )
        },
    ) { outerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(bottom = outerPadding.calculateBottomPadding()),
        ) {
            PredictiveBackPreview(
                enabled = page != AppPage.CHAT,
                onBack = viewModel::dismissPanels,
                beneath = { ChatHome(state, viewModel, activity, listState, snackbar) },
            ) {
                AnimatedContent(
                    targetState = page,
                    transitionSpec = { EnterTransition.None togetherWith ExitTransition.None },
                    modifier = Modifier.fillMaxSize(),
                    label = "appPage",
                ) { current ->
                    Box(Modifier.fillMaxSize().cubeFlip(this)) {
                        when (current) {
                            AppPage.CHAT -> ChatHome(state, viewModel, activity, listState, snackbar)
                            AppPage.CARDS -> CardsUi(state, viewModel)
                            AppPage.WEALTH -> WealthUi(state, viewModel)
                            AppPage.CONTACTS -> ContactsUi(state, viewModel)
                        }
                    }
                }
            }
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
}

/**
 * 屏幕下端现代化导航栏 (Bottom Navigation Bar)。
 *
 * 采用 Material 3 标准规范，支持触控缩放、胶囊高亮背景，并绑定 UiTags 兼容自动化测试。
 */
@Composable
private fun BankBottomNavigation(
    currentPage: AppPage,
    onSelectPage: (AppPage) -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shadowElevation = 8.dp,
        modifier = modifier.fillMaxWidth(),
    ) {
        NavigationBar(
            containerColor = MaterialTheme.colorScheme.surface,
            tonalElevation = 0.dp,
            modifier = Modifier.height(68.dp),
        ) {
            NavigationBarItem(
                selected = currentPage == AppPage.CHAT,
                onClick = { onSelectPage(AppPage.CHAT) },
                icon = {
                    Icon(
                        Icons.Default.Home,
                        contentDescription = "智能助手",
                        modifier = Modifier.size(24.dp),
                    )
                },
                label = {
                    Text(
                        "助手",
                        fontSize = 12.sp,
                        fontWeight = if (currentPage == AppPage.CHAT) FontWeight.Bold else FontWeight.Normal,
                    )
                },
                colors = NavigationBarItemDefaults.colors(
                    selectedIconColor = MaterialTheme.colorScheme.primary,
                    selectedTextColor = MaterialTheme.colorScheme.primary,
                    indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                ),
            )

            NavigationBarItem(
                selected = currentPage == AppPage.CARDS,
                onClick = { onSelectPage(AppPage.CARDS) },
                icon = {
                    Icon(
                        Icons.Default.AccountBox,
                        contentDescription = "我的卡包",
                        modifier = Modifier.size(24.dp),
                    )
                },
                label = {
                    Text(
                        "卡包",
                        fontSize = 12.sp,
                        fontWeight = if (currentPage == AppPage.CARDS) FontWeight.Bold else FontWeight.Normal,
                    )
                },
                colors = NavigationBarItemDefaults.colors(
                    selectedIconColor = MaterialTheme.colorScheme.primary,
                    selectedTextColor = MaterialTheme.colorScheme.primary,
                    indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                ),
                modifier = Modifier.testTag(UiTags.OPEN_CARDS),
            )

            NavigationBarItem(
                selected = currentPage == AppPage.WEALTH,
                onClick = { onSelectPage(AppPage.WEALTH) },
                icon = {
                    Icon(
                        Icons.Default.Star,
                        contentDescription = "财富管理",
                        modifier = Modifier.size(24.dp),
                    )
                },
                label = {
                    Text(
                        "理财",
                        fontSize = 12.sp,
                        fontWeight = if (currentPage == AppPage.WEALTH) FontWeight.Bold else FontWeight.Normal,
                    )
                },
                colors = NavigationBarItemDefaults.colors(
                    selectedIconColor = MaterialTheme.colorScheme.primary,
                    selectedTextColor = MaterialTheme.colorScheme.primary,
                    indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                ),
                modifier = Modifier.testTag(UiTags.OPEN_WEALTH),
            )

            NavigationBarItem(
                selected = currentPage == AppPage.CONTACTS,
                onClick = { onSelectPage(AppPage.CONTACTS) },
                icon = {
                    Icon(
                        Icons.Default.Person,
                        contentDescription = "常用联系人",
                        modifier = Modifier.size(24.dp),
                    )
                },
                label = {
                    Text(
                        "联系人",
                        fontSize = 12.sp,
                        fontWeight = if (currentPage == AppPage.CONTACTS) FontWeight.Bold else FontWeight.Normal,
                    )
                },
                colors = NavigationBarItemDefaults.colors(
                    selectedIconColor = MaterialTheme.colorScheme.primary,
                    selectedTextColor = MaterialTheme.colorScheme.primary,
                    indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                ),
                modifier = Modifier.testTag(UiTags.OPEN_CONTACTS),
            )
        }
    }
}


/**
 * 聊天主页本体。会话列表滚动位置与 Snackbar 状态放在 [ChatScreen] 根上，
 * 访问覆盖页返回后不丢失。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatHome(
    state: ChatUiState,
    viewModel: ChatViewModel,
    activity: FragmentActivity,
    listState: LazyListState,
    snackbar: SnackbarHostState,
) {
    // Keep the newest bubble in view.  A LazyColumn does not follow appended
    // items on its own, so without this the answer to a question scrolls in
    // below the fold and the user sees nothing happen.
    LaunchedEffect(
        state.messages.size,
        state.pending,
        state.messages.lastOrNull()?.text,
        state.messages.lastOrNull()?.traceId,
    ) {
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
                title = {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Box(
                            modifier = Modifier
                                .size(36.dp)
                                .clip(RoundedCornerShape(10.dp))
                                .background(
                                    Brush.linearGradient(
                                        listOf(
                                            MaterialTheme.colorScheme.primary,
                                            MaterialTheme.colorScheme.secondary,
                                        )
                                    )
                                ),
                            contentAlignment = Alignment.Center,
                        ) {
                            Icon(
                                Icons.Default.Lock,
                                contentDescription = null,
                                tint = Color.White,
                                modifier = Modifier.size(20.dp),
                            )
                        }
                        Column {
                            Text(
                                "安言 AI 银行",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                            )
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(4.dp),
                            ) {
                                Box(
                                    modifier = Modifier
                                        .size(6.dp)
                                        .clip(CircleShape)
                                        .background(Color(0xFF10B981))
                                )
                                Text(
                                    "风控就绪 · 确定性沙箱",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                },
                actions = {
                    TextButton(
                        onClick = viewModel::openTasks,
                        modifier = Modifier.testTag(UiTags.OPEN_TASKS),
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                            Icon(Icons.Default.DateRange, contentDescription = null, modifier = Modifier.size(16.dp))
                            Text("任务", style = MaterialTheme.typography.labelMedium)
                        }
                    }
                    TextButton(
                        onClick = viewModel::openDevices,
                        modifier = Modifier.testTag(UiTags.OPEN_DEVICES),
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                            Icon(Icons.Default.Lock, contentDescription = null, modifier = Modifier.size(16.dp))
                            Text("设备", style = MaterialTheme.typography.labelMedium)
                        }
                    }
                    TextButton(
                        onClick = viewModel::logout,
                        modifier = Modifier.testTag(UiTags.LOGOUT),
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                            Icon(
                                Icons.AutoMirrored.Filled.ExitToApp,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp),
                                tint = MaterialTheme.colorScheme.error,
                            )
                            Text(
                                "退出",
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.error,
                            )
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        // 红色风险验证挂起（MFA 审批在台上）：聊天背景进入磨砂玻璃态——
        // 消息/建议/输入行连续模糊并提亮，审批卡保持清晰聚焦。
        val redVerification = state.pending?.mfaRequired == true
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .imePadding(),
        ) {
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth(),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(state.messages) { message ->
                    MessageBubble(message, Modifier.verificationFrost(redVerification))
                }
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
                Box(
                    Modifier
                        .fillMaxWidth()
                        .padding(8.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(28.dp),
                        strokeWidth = 2.5.dp,
                    )
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
                    modifier = Modifier
                        .fillMaxWidth()
                        .verificationFrost(redVerification),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
                ) {
                    items(state.suggestions.size) { index ->
                        SuggestionChip(
                            onClick = { viewModel.onSuggestionClick(state.suggestions[index]) },
                            label = { Text(state.suggestions[index], fontSize = 12.sp) },
                            shape = RoundedCornerShape(16.dp),
                            colors = SuggestionChipDefaults.suggestionChipColors(
                                containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f),
                            ),
                        )
                    }
                }
            }
            InputRow(
                value = state.input,
                enabled = state.canSend,
                onValueChange = viewModel::onInputChange,
                onSend = viewModel::send,
                modifier = Modifier.verificationFrost(redVerification),
            )
        }
    }
}

@Composable
private fun MessageBubble(message: ChatMessage, modifier: Modifier = Modifier) {
    val isUser = message.role == ChatRole.USER
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        if (!isUser) {
            Box(
                modifier = Modifier
                    .padding(top = 4.dp, end = 8.dp)
                    .size(32.dp)
                    .clip(CircleShape)
                    .background(
                        Brush.linearGradient(
                            listOf(
                                MaterialTheme.colorScheme.primary,
                                MaterialTheme.colorScheme.secondary,
                            )
                        )
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    Icons.Default.AccountCircle,
                    contentDescription = "AI 助手",
                    tint = Color.White,
                    modifier = Modifier.size(18.dp),
                )
            }
        }
        Card(
            shape = RoundedCornerShape(
                topStart = 18.dp,
                topEnd = 18.dp,
                bottomStart = if (isUser) 18.dp else 4.dp,
                bottomEnd = if (isUser) 4.dp else 18.dp,
            ),
            colors = CardDefaults.cardColors(
                containerColor = if (isUser) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface,
            ),
            elevation = CardDefaults.cardElevation(defaultElevation = if (isUser) 1.dp else 3.dp),
            modifier = Modifier.fillMaxWidth(if (isUser) 0.85f else 0.88f),
        ) {
            Column(Modifier.padding(12.dp)) {
                Text(
                    text = message.text,
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (isUser) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurface,
                )
                // Structured facts (balances, category totals, anomaly flags,
                // the task DAG, the birthday memo) render below the prose.
                ResultExtras(message.result)
                message.traceId?.let { traceId ->
                    Spacer(Modifier.height(8.dp))
                    Surface(
                        shape = RoundedCornerShape(6.dp),
                        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f),
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
                        ) {
                            Icon(
                                Icons.Default.CheckCircle,
                                contentDescription = null,
                                tint = Color(0xFF10B981),
                                modifier = Modifier.size(12.dp),
                            )
                            Text(
                                text = "链路编号 $traceId",
                                style = MaterialTheme.typography.labelSmall,
                                fontFamily = FontFamily.Monospace,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
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
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(
            containerColor = if (isRed) Color(0xFFFFF1F0) else Color(0xFFFFFBEB),
        ),
        border = androidx.compose.foundation.BorderStroke(
            1.dp,
            if (isRed) Color(0xFFFCA5A5) else Color(0xFFFCD34D),
        ),
        elevation = CardDefaults.cardElevation(defaultElevation = 4.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    if (isRed) Icons.Default.Warning else Icons.Default.Info,
                    contentDescription = null,
                    tint = if (isRed) Color(0xFFDC2626) else Color(0xFFD97706),
                    modifier = Modifier.size(22.dp),
                )
                Text(
                    text = if (isRed) "红色风险：必须完成服务端多因子认证" else "黄色风险：请确认本次操作",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = if (isRed) Color(0xFF991B1B) else Color(0xFF92400E),
                )
            }
            Text(
                approval.summary,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
            )
            Surface(
                shape = RoundedCornerShape(6.dp),
                color = (if (isRed) Color(0xFFFEE2E2) else Color(0xFFFEF3C7)),
            ) {
                Text(
                    text = "审批编号 ${approval.approvalId} · " +
                        if (remainingSeconds > 0) "剩余 $remainingSeconds 秒" else "已超时，请重新发起操作",
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                )
            }
            if (isRed) {
                OutlinedTextField(
                    value = otp,
                    onValueChange = onOtpChange,
                    label = { Text("短信验证码（测试码：888888）") },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.APPROVAL_OTP),
                )
                if (!canPromptBiometric) {
                    Text(
                        "本设备未录入生物识别凭据，无法完成红色风险强验证。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                if (isRed) {
                    Button(
                        onClick = onBiometricApprove,
                        enabled = !busy && canPromptBiometric && otp.length >= 6,
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFDC2626)),
                        modifier = Modifier.testTag(UiTags.APPROVAL_APPROVE),
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                            Icon(Icons.Default.CheckCircle, contentDescription = null, modifier = Modifier.size(18.dp))
                            Text("生物识别签名并执行")
                        }
                    }
                } else {
                    Button(
                        onClick = onApprove,
                        enabled = !busy,
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.testTag(UiTags.APPROVAL_APPROVE),
                    ) { Text("确认执行") }
                }
                OutlinedButton(
                    onClick = onReject,
                    enabled = !busy,
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.testTag(UiTags.APPROVAL_REJECT),
                ) { Text("取消操作") }
            }
            Text(
                "确认由核心银行服务端原子校验与记账；客户端不持有任何写账特权。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
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
    modifier: Modifier = Modifier,
) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shadowElevation = 8.dp,
        modifier = modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                value = value,
                onValueChange = onValueChange,
                placeholder = { Text("例如：给李四转 300 元 / 查一下余额", fontSize = 14.sp) },
                modifier = Modifier
                    .weight(1f)
                    .testTag(UiTags.CHAT_INPUT),
                maxLines = 3,
                shape = RoundedCornerShape(22.dp),
            )
            Button(
                onClick = onSend,
                enabled = enabled,
                shape = RoundedCornerShape(22.dp),
                modifier = Modifier
                    .height(48.dp)
                    .testTag(UiTags.CHAT_SEND),
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Icon(
                        Icons.AutoMirrored.Filled.Send,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                    )
                    Text("发送", fontWeight = FontWeight.Bold)
                }
            }
        }
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
