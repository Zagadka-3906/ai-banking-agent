package com.aibanking.agent.ui.chat

import android.os.SystemClock

import androidx.activity.compose.BackHandler
import androidx.compose.animation.*
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.fragment.app.FragmentActivity
import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.data.DeviceView
import com.aibanking.agent.data.TaskView
import com.aibanking.agent.data.SplitRequestView
import com.aibanking.agent.security.BiometricGate
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.PredictiveBackPreview
import com.aibanking.agent.ui.anim.pageSlideSpec
import com.aibanking.agent.ui.anim.verificationFrost
import com.aibanking.agent.ui.components.*
import com.aibanking.agent.ui.theme.BankSpacing
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** 聊天之外的三个全屏覆盖页；聊天页是层级最浅的主页。 */
private enum class AppPage { CHAT, CARDS, WEALTH, CONTACTS }

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun ChatScreen(viewModel: ChatViewModel, activity: FragmentActivity) {
    val state by viewModel.state.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    val listState = rememberLazyListState()
    LaunchedEffect(state.error, state.notice) {
        // Background session/device setup does not need a notification over the editor.
        val notice = state.notice?.takeUnless {
            it.startsWith("会话有效期") || it == "本机设备已注册，高风险操作可用"
        }
        val message = state.error ?: notice
        if (message != null) {
            snackbar.showSnackbar(message)
        }
        if (state.error != null || state.notice != null) {
            viewModel.dismissMessages()
        }
    }
    // The deadline survives destination changes and Activity recreation.
    val approvalDeadlineMillis = rememberSaveable(state.pending?.approvalId) {
        state.pending?.let { SystemClock.elapsedRealtime() + it.expiresIn.coerceAtLeast(0) * 1_000L } ?: 0L
    }

    // 页面状态映射
    val page = when {
        state.showCards -> AppPage.CARDS
        state.showWealth -> AppPage.WEALTH
        state.showContacts -> AppPage.CONTACTS
        else -> AppPage.CHAT
    }

    // Leave more space for the editor while the keyboard is open.
    Scaffold(
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            if (!WindowInsets.isImeVisible) {
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
            }
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
                beneath = { ChatHome(state, viewModel, activity, listState, approvalDeadlineMillis) },
            ) {
                AnimatedContent(
                    targetState = page,
                    transitionSpec = { pageSlideSpec(AppPage::ordinal) },
                    modifier = Modifier.fillMaxSize(),
                    label = "appPage",
                ) { current ->
                    Box(Modifier.fillMaxSize()) {
                        when (current) {
                            AppPage.CHAT -> ChatHome(state, viewModel, activity, listState, approvalDeadlineMillis)
                            AppPage.CARDS -> CardsUi(state, viewModel)
                            AppPage.WEALTH -> WealthUi(state, viewModel, snackbar)
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
            feishuBindingCode = state.feishuBindingCode,
            feishuCodeExpiresAtMillis = state.feishuCodeExpiresAtMillis,
            feishuBusy = state.feishuBusy,
            currentDeviceId = viewModel.currentDeviceId(),
            onRevoke = viewModel::revokeDevice,
            onIssueFeishuCode = viewModel::issueFeishuBindingCode,
            onUnbindFeishu = viewModel::unbindFeishu,
            onDismiss = viewModel::dismissPanels,
        )
    }
    if (state.showTasks) {
        TaskPanel(
            tasks = state.tasks,
            splitRequests = state.splitRequests,
            busy = state.panelBusy,
            onCancel = viewModel::cancelTask,
            onPaySplit = viewModel::prepareSplitPayment,
            onDeclineSplit = viewModel::declineSplit,
            onDismiss = viewModel::dismissPanels,
        )
    }
}

/** Material navigation keeps every destination reachable and gives selection a quiet spring. */
@Composable
private fun BankBottomNavigation(
    currentPage: AppPage,
    onSelectPage: (AppPage) -> Unit,
    modifier: Modifier = Modifier,
) {
    val destinations = listOf(
        Triple(AppPage.CHAT, "助手", BankIconKind.Chat),
        Triple(AppPage.CARDS, "卡包", BankIconKind.Card),
        Triple(AppPage.WEALTH, "理财", BankIconKind.Chart),
        Triple(AppPage.CONTACTS, "联系人", BankIconKind.People),
    )
    Surface(color = MaterialTheme.colorScheme.surface, modifier = modifier.fillMaxWidth()) {
        Column {
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))
            NavigationBar(containerColor = MaterialTheme.colorScheme.surface, tonalElevation = 0.dp) {
                destinations.forEach { (page, label, icon) ->
                    val selected = currentPage == page
                    val scale by animateFloatAsState(if (selected) 1.08f else 1f, spring(dampingRatio = 0.85f), label = "navIcon")
                    val tag = when (page) {
                        AppPage.CHAT -> "open_assistant"
                        AppPage.CARDS -> UiTags.OPEN_CARDS
                        AppPage.WEALTH -> UiTags.OPEN_WEALTH
                        AppPage.CONTACTS -> UiTags.OPEN_CONTACTS
                    }
                    NavigationBarItem(
                        selected = selected, onClick = { if (!selected) onSelectPage(page) },
                        icon = { BankIcon(icon, Modifier.size(24.dp).graphicsLayer { scaleX = scale; scaleY = scale }) },
                        label = { Text(label, style = MaterialTheme.typography.labelMedium, fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = MaterialTheme.colorScheme.primary,
                            selectedTextColor = MaterialTheme.colorScheme.primary,
                            indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                            unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
                        modifier = Modifier.testTag(tag),
                    )
                }
            }
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
    approvalDeadlineMillis: Long?,
) {
    val showWelcome = state.messages.none { it.role == ChatRole.USER }
    // Keep the newest bubble in view.  A LazyColumn does not follow appended
    // items on its own, so without this the answer to a question scrolls in
    // below the fold and the user sees nothing happen.
    LaunchedEffect(
        state.messages.size,
        state.pending,
        state.quiz?.answered,
        state.messages.lastOrNull()?.text,
        state.messages.lastOrNull()?.traceId,
    ) {
        val itemCount = state.messages.size + (if (state.pending != null) 1 else 0) + (if (state.quiz != null) 1 else 0) + (if (showWelcome) 1 else 0)
        if (itemCount > 0 && (!showWelcome || state.pending != null)) {
            snapshotFlow { listState.layoutInfo.totalItemsCount }.first { it >= itemCount }
            listState.animateScrollToItem(itemCount - 1)
        }
    }

    Scaffold(
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Box(Modifier.size(40.dp).clip(MaterialTheme.shapes.medium).background(MaterialTheme.colorScheme.primary), contentAlignment = Alignment.Center) {
                            BankIcon(BankIconKind.Bank, Modifier.size(24.dp), tint = MaterialTheme.colorScheme.onPrimary)
                        }
                        Column {
                            Text("安言 AI 银行", style = MaterialTheme.typography.titleMedium)
                            Text("随时为您，安心打理", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                },
                actions = {
                    IconButton(onClick = viewModel::openTasks, modifier = Modifier.testTag(UiTags.OPEN_TASKS)) {
                        BankIcon(BankIconKind.Check, contentDescription = "待办任务")
                    }
                    IconButton(onClick = viewModel::openDevices, modifier = Modifier.testTag(UiTags.OPEN_DEVICES)) {
                        BankIcon(BankIconKind.Shield, contentDescription = "设备管理")
                    }
                    IconButton(onClick = viewModel::logout, modifier = Modifier.testTag(UiTags.LOGOUT)) {
                        BankIcon(BankIconKind.Logout, contentDescription = "退出登录")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
    ) { padding ->
        // Keep transaction context legible while the approval asks for confirmation.
        val redVerification = state.pending?.mfaRequired == true
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .consumeWindowInsets(padding)
                .imePadding(),
        ) {
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth().testTag(UiTags.CHAT_LIST),
                contentPadding = PaddingValues(horizontal = 20.dp, vertical = 16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                if (showWelcome) {
                    item(key = "welcome") { ChatWelcome(onSuggestion = viewModel::onSuggestionClick) }
                }
                items(state.messages, key = { it.id }) { message ->
                    MessageBubble(message, Modifier.animateItem().verificationFrost(redVerification))
                }
                state.pending?.let { approval ->
                    item(key = "approval:${approval.approvalId}") {
                        ApprovalCard(
                            approval = approval,
                            deadlineMillis = requireNotNull(approvalDeadlineMillis),
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
                state.quiz?.let { quiz ->
                    item(key = "risk_quiz") {
                        RiskQuizPanel(quiz, state.busy, viewModel::onQuizAnswer, viewModel::giveUpQuiz)
                    }
                }
            }
            AnimatedVisibility(state.busy) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp, Alignment.CenterHorizontally),
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                    Text("安言正在为您处理…", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            // Predicted next requests stay directly above the editor. A tap sends
            // immediately; write operations still enter the server approval flow.
            if (state.suggestions.isNotEmpty() && state.quiz == null) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag(UiTags.QUICK_SUGGESTIONS)
                        .verificationFrost(redVerification),
                ) {
                    Text(
                        "猜你想问",
                        modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    LazyRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        contentPadding = PaddingValues(horizontal = 20.dp, vertical = 0.dp),
                    ) {
                        itemsIndexed(state.suggestions, key = { _, suggestion -> suggestion }) { index, suggestion ->
                            SuggestionChip(
                                onClick = { viewModel.onSuggestionClick(suggestion) },
                                enabled = !state.busy,
                                modifier = Modifier.testTag(UiTags.quickSuggestion(index)),
                                label = { Text(suggestion, style = MaterialTheme.typography.labelMedium) },
                                colors = SuggestionChipDefaults.suggestionChipColors(
                                    containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f),
                                ),
                            )
                        }
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
private fun ChatWelcome(onSuggestion: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(18.dp)) {
        BankHeroCard {
            Text("您的日常银行", style = MaterialTheme.typography.labelSmall)
            Spacer(Modifier.height(14.dp))
            Text("财务大小事，\n交给一句话。", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(10.dp))
            Text("查余额、转账、做规划，安言陪您一起打理。", style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(20.dp))
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                BankIcon(BankIconKind.Shield, Modifier.size(16.dp))
                Text("每笔资金操作，始终由您确认", style = MaterialTheme.typography.labelSmall)
            }
        }
        Text("从这里开始", style = MaterialTheme.typography.titleMedium)
        val actions = listOf(
            Triple("查余额", "查一下余额", BankIconKind.Wallet),
            Triple("转一笔", "我想转账", BankIconKind.Transfer),
            Triple("看账单", "查看最近的交易记录", BankIconKind.Card),
            Triple("做规划", "我想做风险测评", BankIconKind.Chart),
        )
        actions.chunked(2).forEach { row ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                row.forEach { (label, prompt, icon) ->
                    Surface(onClick = { onSuggestion(prompt) }, shape = MaterialTheme.shapes.large,
                        color = MaterialTheme.colorScheme.surface, border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f)),
                        modifier = Modifier.weight(1f)) {
                        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            BankIcon(icon, Modifier.size(22.dp), tint = MaterialTheme.colorScheme.primary)
                            Text(label, style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                            BankIcon(BankIconKind.ArrowForward, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
        Text("与安言对话", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 4.dp))
    }
}

@Composable
private fun MessageBubble(message: ChatMessage, modifier: Modifier = Modifier) {
    if (message.role == ChatRole.ASSISTANT) {
        AgentReplyCard(message, modifier)
        return
    }
    Column(modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalAlignment = Alignment.End) {
        Surface(
            shape = RoundedCornerShape(topStart = 22.dp, topEnd = 22.dp, bottomEnd = 6.dp, bottomStart = 22.dp),
            color = MaterialTheme.colorScheme.primaryContainer,
            modifier = Modifier.widthIn(max = 480.dp).fillMaxWidth(0.88f),
        ) {
            Text(message.text, modifier = Modifier.padding(horizontal = 18.dp, vertical = 14.dp),
                style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onPrimaryContainer)
        }
    }
}

@Composable
private fun ApprovalCard(
    approval: ApprovalView,
    deadlineMillis: Long,
    otp: String,
    busy: Boolean,
    canPromptBiometric: Boolean,
    onOtpChange: (String) -> Unit,
    onReject: () -> Unit,
    onApprove: () -> Unit,
    onBiometricApprove: () -> Unit,
) {
    val isRed = approval.mfaRequired
    val focusManager = LocalFocusManager.current
    val keyboardController = LocalSoftwareKeyboardController.current
    fun secondsLeft(): Int = ((deadlineMillis - SystemClock.elapsedRealtime()).coerceAtLeast(0) + 999L).div(1_000L).toInt()
    var remainingSeconds by remember(approval.approvalId, deadlineMillis) { mutableIntStateOf(secondsLeft()) }
    LaunchedEffect(approval.approvalId, deadlineMillis) {
        do {
            remainingSeconds = secondsLeft()
            if (remainingSeconds > 0) delay(250)
        } while (remainingSeconds > 0)
    }
    val riskColors = LocalBankSemanticColors.current
    BankSectionCard(outlined = true) {
        Column(verticalArrangement = Arrangement.spacedBy(BankSpacing.sm)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    if (isRed) Icons.Default.Warning else Icons.Default.Info,
                    contentDescription = null,
                    tint = if (isRed) riskColors.riskRed else riskColors.riskYellow,
                    modifier = Modifier.size(22.dp),
                )
                Text(
                    text = if (isRed) "红色风险：需要验证身份" else "黄色风险：请确认本次操作",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = if (isRed) riskColors.riskRed else riskColors.riskYellow,
                )
            }
            Text(
                approval.summary,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
            )
            Surface(
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Text(
                    text = if (remainingSeconds > 0) "请在 $remainingSeconds 秒内确认" else "已超时，请重新发起操作",
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
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                BankPrimaryButton(
                    text = if (isRed) "生物识别签名并执行" else "确认执行",
                    onClick = {
                        remainingSeconds = secondsLeft()
                        if (remainingSeconds > 0) {
                            keyboardController?.hide()
                            focusManager.clearFocus()
                            if (isRed) onBiometricApprove() else onApprove()
                        }
                    },
                    enabled = !busy && remainingSeconds > 0 && (!isRed || (canPromptBiometric && otp.length >= 6)),
                    loading = busy,
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.APPROVAL_APPROVE),
                )
                BankSecondaryButton("取消操作", onReject, enabled = !busy,
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.APPROVAL_REJECT))
            }
            Text(
                "请核对收款人和金额。只有确认后，才会继续执行本次操作。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/**
 * 逐题风险测评组件：随会话滚动，点选即进下一题；
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
    BankSectionCard(modifier = Modifier.fillMaxWidth().padding(horizontal = BankSpacing.sm, vertical = BankSpacing.xxs)) {
        Column(Modifier.fillMaxWidth().animateContentSize(), verticalArrangement = Arrangement.spacedBy(BankSpacing.xs)) {
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
                    shape = MaterialTheme.shapes.small,
                    color = MaterialTheme.colorScheme.surface,
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                ) {
                    Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
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
    val focus = LocalFocusManager.current
    val send = { if (enabled) { focus.clearFocus(); onSend() } }
    Surface(color = MaterialTheme.colorScheme.background, modifier = modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedTextField(
                value = value, onValueChange = onValueChange,
                placeholder = { Text("说说您想办的事…", style = MaterialTheme.typography.bodyMedium) },
                shape = MaterialTheme.shapes.large,
                colors = OutlinedTextFieldDefaults.colors(unfocusedContainerColor = MaterialTheme.colorScheme.surface,
                    focusedContainerColor = MaterialTheme.colorScheme.surface, unfocusedBorderColor = MaterialTheme.colorScheme.outlineVariant),
                modifier = Modifier.weight(1f).testTag(UiTags.CHAT_INPUT), maxLines = 4,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { send() }),
            )
            FilledIconButton(onClick = send, enabled = enabled,
                shape = MaterialTheme.shapes.large, modifier = Modifier.size(56.dp).testTag(UiTags.CHAT_SEND)) {
                BankIcon(BankIconKind.Send, contentDescription = "发送")
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DevicePanel(
    devices: List<DeviceView>,
    busy: Boolean,
    feishuBindingCode: String?,
    feishuCodeExpiresAtMillis: Long,
    feishuBusy: Boolean,
    currentDeviceId: String?,
    onRevoke: (String) -> Unit,
    onIssueFeishuCode: () -> Unit,
    onUnbindFeishu: () -> Unit,
    onDismiss: () -> Unit,
) {
    // The sheet window never takes focus, so system Back reaches the Activity
    // dispatcher and would finish the app instead of closing the panel.
    BackHandler(enabled = true) { onDismiss() }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.background) {
        LazyColumn(contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp), modifier = Modifier.fillMaxWidth()) {
            item {
                BankScreenHeading("已注册设备", subtitle = "管理可访问您账户的设备", eyebrow = "设备安全")
            }
            item {
                BankSectionCard {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("飞书私聊查询", style = MaterialTheme.typography.titleMedium)
                        Text("获取一次性绑定码后，在飞书机器人私聊发送“/绑定 绑定码”。余额与账单可在飞书查询；转账和审批仍需回到本应用。",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (feishuBindingCode != null && System.currentTimeMillis() < feishuCodeExpiresAtMillis) {
                            SelectionContainer {
                                Text("/绑定 $feishuBindingCode", style = MaterialTheme.typography.bodyMedium, fontFamily = FontFamily.Monospace)
                            }
                            Text("绑定码 5 分钟内有效，仅在此处显示一次。", style = MaterialTheme.typography.bodySmall)
                        }
                        BankSecondaryButton("获取绑定码", onIssueFeishuCode, enabled = !feishuBusy, modifier = Modifier.fillMaxWidth())
                        TextButton(onClick = onUnbindFeishu, enabled = !feishuBusy) { Text("解除飞书绑定") }
                    }
                }
            }
            if (busy) item { BankLoadingState(message = "正在更新设备…") }
            if (!busy && devices.isEmpty()) item { BankEmptyState("没有已注册的设备。") }
            items(devices, key = { it.deviceId }) { device ->
                BankSectionCard(modifier = Modifier.animateItem()) {
                    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            BankIcon(BankIconKind.Shield, Modifier.size(28.dp), tint = MaterialTheme.colorScheme.primary)
                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Text(device.label ?: "未知设备", style = MaterialTheme.typography.titleMedium)
                            }
                            BankStatusChip(if (device.status == "ACTIVE") "已启用" else "已停用",
                                if (device.status == "ACTIVE") BankSemanticStatus.RiskGreen else BankSemanticStatus.Neutral)
                        }
                        if (device.createdAt > 0) Text("注册于 ${formatEpochSeconds(device.createdAt)}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        var showDetails by rememberSaveable(device.deviceId) { mutableStateOf(false) }
                        TextButton(onClick = { showDetails = !showDetails }, contentPadding = PaddingValues(0.dp)) {
                            Text(if (showDetails) "收起设备信息" else "查看设备信息", style = MaterialTheme.typography.labelMedium)
                        }
                        AnimatedVisibility(showDetails) {
                            Text("${device.deviceId}\n指纹 ${device.fingerprint.take(16)}…", style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace)
                        }
                        if (device.deviceId == currentDeviceId && device.status == "ACTIVE") {
                            Text("这是本机：停用后红色风险操作在本机将不可用。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                        }
                        if (device.status == "ACTIVE") {
                            BankSecondaryButton("停用", { onRevoke(device.deviceId) }, enabled = !busy,
                                modifier = Modifier.fillMaxWidth().testTag(UiTags.DEVICE_REVOKE))
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
    splitRequests: List<SplitRequestView>,
    busy: Boolean,
    onCancel: (String) -> Unit,
    onPaySplit: (SplitRequestView) -> Unit,
    onDeclineSplit: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    // The sheet window never takes focus, so system Back reaches the Activity
    // dispatcher and would finish the app instead of closing the panel.
    BackHandler(enabled = true) { onDismiss() }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.background) {
        LazyColumn(contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp), modifier = Modifier.fillMaxWidth()) {
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
                            BankIcon(BankIconKind.Check, Modifier.size(26.dp), tint = MaterialTheme.colorScheme.primary)
                            Text(taskTypeLabel(task.taskType), style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                            BankStatusChip(taskStatusLabel(task.status), when (task.status) {
                                "COMPLETED", "SUCCESS", "SUCCEEDED" -> BankSemanticStatus.RiskGreen
                                "FAILED" -> BankSemanticStatus.RiskRed
                                "WAITING_USER" -> BankSemanticStatus.RiskYellow
                                else -> BankSemanticStatus.Neutral
                            })
                        }
                        taskSummary(task)?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                        formatLocalTimestamp(task.runAt)?.let { Text("计划时间 · $it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                        if (task.status in CANCELLABLE_TASK_STATUSES) {
                            BankSecondaryButton("取消", { onCancel(task.taskId) }, enabled = !busy,
                                modifier = Modifier.fillMaxWidth().testTag(UiTags.TASK_CANCEL))
                        }
                    }
                }
            }
            item {
                Text("到期需要资金的任务会先暂停并显示在任务列表，确认后才会执行。", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(vertical = 8.dp))
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

private fun formatEpochSeconds(epochSeconds: Long): String =
    runCatching {
        DateTimeFormatter.ofPattern("yyyy-MM-dd")
            .withZone(ZoneId.systemDefault())
            .format(Instant.ofEpochSecond(epochSeconds))
    }.getOrDefault("")
