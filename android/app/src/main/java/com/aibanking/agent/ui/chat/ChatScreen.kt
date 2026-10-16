package com.aibanking.agent.ui.chat

import android.os.SystemClock

import androidx.activity.compose.BackHandler
import androidx.compose.animation.*
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.fragment.app.FragmentActivity
import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.data.DeviceView
import com.aibanking.agent.data.TaskView
import com.aibanking.agent.data.SplitRequestView
import com.aibanking.agent.security.BiometricGate
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.PredictiveBackPreview
import com.aibanking.agent.ui.anim.GradientRevealText
import com.aibanking.agent.ui.anim.ShimmerGradientText
import com.aibanking.agent.ui.anim.bankPressScale
import com.aibanking.agent.ui.anim.breathe
import com.aibanking.agent.ui.anim.popOnChange
import com.aibanking.agent.ui.anim.rememberEntranceTracker
import com.aibanking.agent.ui.anim.riseIn
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.interaction.MutableInteractionSource
import com.aibanking.agent.ui.anim.pageSlideSpec
import com.aibanking.agent.ui.anim.verificationFrost
import com.aibanking.agent.ui.components.*
import com.aibanking.agent.ui.theme.BankSpacing
import com.aibanking.agent.ui.theme.BankPalette
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
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
    // Keep the deadline and notifications alive while viewing a receipt.
    // Leaving their composition would restart the approval clock on return.
    val resultMessage = state.messages.firstOrNull { it.id == state.outcomeMessageId }
    val outcome = resultMessage?.let(::outcomeFor) ?: state.lastProblem?.takeIf { state.showProblem }?.let(::outcomeFor)
    if (outcome != null) {
        OperationOutcomeScreen(outcome, resultMessage, state.lastProblem.takeIf { state.showProblem },
            canQuery = state.authenticated && !state.busy && state.pending == null && state.quiz == null,
            onBack = viewModel::closeOutcome, onBalance = viewModel::outcomeBalance, onTasks = viewModel::outcomeTasks,
            onReconcile = { resultMessage?.let { viewModel.reconcileMessage(it.id) } }, snackbarHostState = snackbar)
        return
    }

    // 页面状态映射
    val page = when {
        state.showCards -> AppPage.CARDS
        state.showWealth -> AppPage.WEALTH
        state.showContacts -> AppPage.CONTACTS
        else -> AppPage.CHAT
    }

    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val drawerScope = rememberCoroutineScope()
    fun closeDrawerThen(action: () -> Unit) {
        drawerScope.launch { drawerState.close() }
        action()
    }
    BackHandler(enabled = drawerState.isOpen) { drawerScope.launch { drawerState.close() } }
    // Conversation first: every other destination lives in the side drawer.
    ModalNavigationDrawer(
        drawerState = drawerState,
        gesturesEnabled = page == AppPage.CHAT || drawerState.isOpen,
        drawerContent = {
            BankNavigationDrawer(
                open = drawerState.targetValue == DrawerValue.Open,
                currentPage = page,
                principal = state.principal,
                onChat = { closeDrawerThen(viewModel::dismissPanels) },
                onCards = { closeDrawerThen(viewModel::openCards) },
                onWealth = { closeDrawerThen(viewModel::openWealth) },
                onContacts = { closeDrawerThen(viewModel::openContacts) },
                onTasks = { closeDrawerThen(viewModel::openTasks) },
                onDevices = { closeDrawerThen(viewModel::openDevices) },
                onLogout = { closeDrawerThen(viewModel::logout) },
            )
        },
    ) {
        Scaffold(
            contentWindowInsets = WindowInsets(0, 0, 0, 0),
            snackbarHost = { SnackbarHost(snackbar) },
            containerColor = MaterialTheme.colorScheme.background,
        ) { outerPadding ->
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(bottom = outerPadding.calculateBottomPadding())
                    .navigationBarsPadding(),
            ) {
                PredictiveBackPreview(
                    enabled = page != AppPage.CHAT,
                    onBack = viewModel::dismissPanels,
                    beneath = { ChatHome(state, viewModel, activity, listState, approvalDeadlineMillis, onMenu = {}) },
                ) {
                    AnimatedContent(
                        targetState = page,
                        transitionSpec = { pageSlideSpec(AppPage::ordinal) },
                        modifier = Modifier.fillMaxSize(),
                        label = "appPage",
                    ) { current ->
                        Box(Modifier.fillMaxSize()) {
                            when (current) {
                                AppPage.CHAT -> ChatHome(state, viewModel, activity, listState, approvalDeadlineMillis,
                                    onMenu = { drawerScope.launch { drawerState.open() } })
                                AppPage.CARDS -> CardsUi(state, viewModel)
                                AppPage.WEALTH -> WealthUi(state, viewModel, snackbar)
                                AppPage.CONTACTS -> ContactsUi(state, viewModel)
                            }
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
            onTakeover = viewModel::takeoverTask,
            onPaySplit = viewModel::prepareSplitPayment,
            onDeclineSplit = viewModel::declineSplit,
            onDismiss = viewModel::dismissPanels,
        )
    }
}

private data class DrawerEntry(val label: String, val icon: BankIconKind, val tag: String, val page: AppPage?, val onClick: () -> Unit)

/** Side drawer in the manner of Gemini: brand header, destinations, account footer. */
@Composable
private fun BankNavigationDrawer(
    open: Boolean,
    currentPage: AppPage,
    principal: String,
    onChat: () -> Unit,
    onCards: () -> Unit,
    onWealth: () -> Unit,
    onContacts: () -> Unit,
    onTasks: () -> Unit,
    onDevices: () -> Unit,
    onLogout: () -> Unit,
) {
    ModalDrawerSheet(
        drawerContainerColor = MaterialTheme.colorScheme.surfaceContainerLow,
        drawerShape = RoundedCornerShape(topEnd = 28.dp, bottomEnd = 28.dp),
        modifier = Modifier.widthIn(max = 320.dp).testTag(UiTags.NAV_DRAWER),
    ) {
        Column(Modifier.fillMaxHeight().verticalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 16.dp)) {
            Row(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                BankGradientMark(Modifier.size(28.dp))
                Text("安言", style = MaterialTheme.typography.titleLarge)
            }
            Spacer(Modifier.height(12.dp))
            val main = listOf(
                DrawerEntry("对话", BankIconKind.Chat, "open_assistant", AppPage.CHAT, onChat),
                DrawerEntry("卡包", BankIconKind.Card, UiTags.OPEN_CARDS, AppPage.CARDS, onCards),
                DrawerEntry("理财", BankIconKind.Chart, UiTags.OPEN_WEALTH, AppPage.WEALTH, onWealth),
                DrawerEntry("联系人", BankIconKind.People, UiTags.OPEN_CONTACTS, AppPage.CONTACTS, onContacts),
            )
            val tools = listOf(
                DrawerEntry("待办任务", BankIconKind.Task, UiTags.OPEN_TASKS, null, onTasks),
                DrawerEntry("设备与飞书", BankIconKind.Shield, UiTags.OPEN_DEVICES, null, onDevices),
            )
            main.forEachIndexed { index, entry -> DrawerItem(entry, selected = entry.page == currentPage, open = open, index = index) }
            Text("服务", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 16.dp, top = 20.dp, bottom = 8.dp))
            tools.forEachIndexed { index, entry -> DrawerItem(entry, selected = false, open = open, index = main.size + index) }
            Spacer(Modifier.height(24.dp))
            HorizontalDivider(Modifier.padding(horizontal = 12.dp, vertical = 12.dp), color = MaterialTheme.colorScheme.outlineVariant)
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                AccountAvatar(principal)
                Column(Modifier.weight(1f)) {
                    Text(principal.ifBlank { "已登录" }, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text("演示账户 · 本机模拟银行", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            DrawerItem(DrawerEntry("退出登录", BankIconKind.Logout, UiTags.LOGOUT, null, onLogout), selected = false,
                open = open, index = main.size + tools.size)
        }
    }
}

@Composable
private fun DrawerItem(entry: DrawerEntry, selected: Boolean, open: Boolean = false, index: Int = 0) {
    // Re-keyed on every opening so the destinations cascade in from the left.
    key(open) {
    NavigationDrawerItem(
        label = { Text(entry.label, style = MaterialTheme.typography.labelLarge) },
        icon = { BankIcon(entry.icon, Modifier.size(22.dp)) },
        selected = selected,
        onClick = entry.onClick,
        shape = CircleShape,
        colors = NavigationDrawerItemDefaults.colors(
            unselectedContainerColor = Color.Transparent,
            selectedContainerColor = MaterialTheme.colorScheme.primaryContainer,
            selectedTextColor = MaterialTheme.colorScheme.onPrimaryContainer,
            selectedIconColor = MaterialTheme.colorScheme.onPrimaryContainer,
        ),
        modifier = Modifier.heightIn(min = 52.dp).testTag(entry.tag)
            .riseIn(animate = open, delayMillis = 40 + index * 35, durationMillis = 260, rise = 0.dp, fromX = (-16).dp),
    )
    }
}

/** Initial-letter avatar on the brand gradient. */
@Composable
internal fun AccountAvatar(principal: String, modifier: Modifier = Modifier, onClick: (() -> Unit)? = null) {
    val initial = principal.removePrefix("user_").firstOrNull()?.uppercaseChar()?.toString() ?: "我"
    Box(
        modifier.size(36.dp).clip(CircleShape).background(LocalBankSemanticColors.current.brandGradient)
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClickLabel = "打开菜单", onClick = onClick) else Modifier),
        contentAlignment = Alignment.Center,
    ) {
        Text(initial, style = MaterialTheme.typography.titleSmall, color = Color.White)
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
    onMenu: () -> Unit,
) {
    val showWelcome = state.messages.none { it.role == ChatRole.USER }
    // Messages restored from storage appear at once; new turns slide into place.
    val entrance = rememberEntranceTracker(state.messages.map { it.id })
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
        containerColor = MaterialTheme.colorScheme.background,
        topBar = { BankChatTopBar(principal = state.principal, onMenu = onMenu) },
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
                contentPadding = PaddingValues(horizontal = 20.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                if (showWelcome) {
                    item(key = "welcome") { ChatWelcome(onSuggestion = viewModel::onSuggestionClick) }
                }
                items(state.messages, key = { it.id }) { message ->
                    val fresh = remember(message.id) { entrance.isNew(message.id) }
                    val arrive = if (message.role == ChatRole.USER) Modifier.riseIn(fresh, rise = 6.dp, fromX = 28.dp, durationMillis = 280)
                        else Modifier.riseIn(fresh, delayMillis = 60, rise = 18.dp, durationMillis = 420)
                    MessageBubble(message, Modifier.animateItem().then(arrive).verificationFrost(redVerification),
                        onReconcile = { viewModel.reconcileMessage(message.id) }, onOutcome = { viewModel.openOutcome(message.id) })
                }
                state.pending?.let { approval ->
                    item(key = "approval:${approval.approvalId}") {
                        ApprovalCard(
                            modifier = Modifier.riseIn(delayMillis = 80, rise = 24.dp, fromScale = 0.96f, durationMillis = 380),
                            approval = approval,
                            deadlineMillis = requireNotNull(approvalDeadlineMillis),
                            otp = state.otp,
                            busy = state.busy,
                            onOtpChange = viewModel::onOtpChange,
                            onRequestOtp = viewModel::requestDemoOtp,
                            onReject = viewModel::reject,
                            onApprove = viewModel::approve,
                            onBiometricApprove = {
                                // Prove presence locally first; the server still
                                // verifies the device signature it receives.
                                val callbacks = viewModel.deviceApprovalCallbacks(approval.approvalId)
                                BiometricGate.prompt(
                                    activity = activity,
                                    title = "确认高风险操作",
                                    subtitle = approval.summary,
                                    onSuccess = callbacks.onSuccess,
                                    onFailure = callbacks.onFailure,
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
                        .padding(horizontal = 20.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    ThinkingMark()
                    ShimmerGradientText("安言正在为您处理…", LocalBankSemanticColors.current.brandGradientColors,
                        style = MaterialTheme.typography.bodyMedium)
                }
            }
            state.lastProblem?.let {
                BankSecondaryButton("查看原因与下一步", viewModel::openProblem,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 4.dp).testTag(UiTags.OPEN_FAILURE))
            }
            // Predicted next requests stay directly above the editor. A tap sends
            if (state.busy && state.activeStages.isNotEmpty()) {
                ReplyPanel(Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) { WorkflowProgress(state.activeStages) }
            }
            // immediately; write operations still enter the server approval flow.
            if (state.suggestions.isNotEmpty() && state.quiz == null) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag(UiTags.QUICK_SUGGESTIONS)
                        .verificationFrost(redVerification),
                ) {
                    LazyRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        contentPadding = PaddingValues(horizontal = 20.dp, vertical = 0.dp),
                    ) {
                        itemsIndexed(state.suggestions, key = { _, suggestion -> suggestion }) { index, suggestion ->
                            SuggestionChip(
                                onClick = { viewModel.onSuggestionClick(suggestion) },
                                enabled = !state.busy,
                                modifier = Modifier.testTag(UiTags.quickSuggestion(index))
                                    .riseIn(delayMillis = 120 + index * 60, rise = 10.dp, fromScale = 0.92f, durationMillis = 260),
                                label = { Text(suggestion, style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(vertical = 10.dp)) },
                                shape = RoundedCornerShape(16.dp),
                                border = null,
                                colors = SuggestionChipDefaults.suggestionChipColors(
                                    containerColor = MaterialTheme.colorScheme.surfaceContainer,
                                    labelColor = MaterialTheme.colorScheme.onSurface,
                                ),
                            )
                        }
                    }
                }
            }
            InputRow(
                value = state.input,
                enabled = state.canSend,
                voiceEnabled = state.authenticated && !state.busy,
                captureVoiceResult = viewModel::captureVoiceResult,
                cloudSpeechAvailable = viewModel::cloudSpeechAvailable,
                transcribeCloud = viewModel::transcribeCloud,
                onValueChange = viewModel::onInputChange,
                onSend = viewModel::send,
                modifier = Modifier.verificationFrost(redVerification),
            )
        }
    }
}

/** Menu, product name and account avatar - the Gemini top bar, nothing else. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun BankChatTopBar(principal: String, onMenu: () -> Unit) {
    TopAppBar(
        navigationIcon = {
            IconButton(onClick = onMenu, modifier = Modifier.testTag(UiTags.OPEN_DRAWER)) {
                BankIcon(BankIconKind.Menu, contentDescription = "打开菜单")
            }
        },
        title = { Text("安言", style = MaterialTheme.typography.titleLarge, maxLines = 1) },
        actions = { AccountAvatar(principal, Modifier.padding(end = 12.dp), onClick = onMenu) },
        colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
    )
}

private data class WelcomeAction(val label: String, val prompt: String, val icon: BankIconKind, val readOnly: Boolean)

/** Gradient greeting and four starter cards, shown until the first message. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ChatWelcome(onSuggestion: (String) -> Unit) {
    val semantic = LocalBankSemanticColors.current
    Column(Modifier.fillMaxWidth().padding(top = 24.dp), verticalArrangement = Arrangement.spacedBy(28.dp)) {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            // The session carries a login ID, not a display name, so the greeting stays generic.
            GradientRevealText("你好，我是安言", semantic.brandGradientColors, style = MaterialTheme.typography.displaySmall)
            Text("今天想办点什么？", style = MaterialTheme.typography.displaySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.55f),
                modifier = Modifier.riseIn(delayMillis = 450, rise = 10.dp, durationMillis = 500))
        }
        val actions = listOf(
            WelcomeAction("查询余额", "查一下余额", BankIconKind.Wallet, true),
            WelcomeAction("转一笔钱", "我想转账", BankIconKind.Transfer, false),
            WelcomeAction("分析账单", "上月钱花哪儿了", BankIconKind.Chart, true),
            WelcomeAction("查看订阅", "查看我的订阅", BankIconKind.Card, true),
        )
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val columns = if (maxWidth < 300.dp || LocalDensity.current.fontScale > 1.3f) 1 else 2
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                actions.chunked(columns).forEachIndexed { rowIndex, row ->
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        row.forEachIndexed { columnIndex, (label, prompt, icon, readOnly) ->
                            val order = rowIndex * columns + columnIndex
                            val press = remember { MutableInteractionSource() }
                            Surface(onClick = { onSuggestion(prompt) }, shape = RoundedCornerShape(20.dp),
                                color = MaterialTheme.colorScheme.surfaceContainer, interactionSource = press,
                                modifier = Modifier.weight(1f)
                                    .riseIn(delayMillis = 600 + order * 80, rise = 20.dp, durationMillis = 420)
                                    .bankPressScale(press)) {
                                Column(Modifier.heightIn(min = 96.dp).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                                    Box(Modifier.size(32.dp).clip(CircleShape)
                                        .background(if (readOnly) semantic.riskGreenContainer else MaterialTheme.colorScheme.primaryContainer),
                                        contentAlignment = Alignment.Center) {
                                        BankIcon(icon, Modifier.size(18.dp),
                                            tint = if (readOnly) semantic.onRiskContainer else MaterialTheme.colorScheme.onPrimaryContainer)
                                    }
                                    Text(label, style = MaterialTheme.typography.titleSmall)
                                    Text(if (readOnly) "直接查询" else "补充信息后再确认", style = MaterialTheme.typography.labelMedium,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        }
                    }
                }
            }
        }
        Column(Modifier.riseIn(delayMillis = 950, rise = 8.dp, durationMillis = 400), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("查询直接回复，资金操作先确认。", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                BankStatusChip("绿 · 只读", BankSemanticStatus.RiskGreen)
                BankStatusChip("黄 · 需确认", BankSemanticStatus.RiskYellow)
                BankStatusChip("红 · 强验证", BankSemanticStatus.RiskRed)
            }
        }
    }
}

/** Slowly turning brand mark shown while a turn is being processed. */
@Composable
private fun ThinkingMark() {
    val motion = com.aibanking.agent.ui.anim.LocalBankMotionEnabled.current
    val angle = if (motion) {
        val transition = rememberInfiniteTransition(label = "thinking")
        transition.animateFloat(0f, 360f, infiniteRepeatable(tween(2400, easing = LinearEasing)), label = "spin").value
    } else 0f
    BankGradientMark(Modifier.size(22.dp).rotate(angle))
}

@Composable
private fun MessageBubble(message: ChatMessage, modifier: Modifier = Modifier, onReconcile: () -> Unit = {}, onOutcome: () -> Unit = {}) {
    if (message.role == ChatRole.ASSISTANT) {
        AgentReplyCard(message, modifier, onReconcile, onOutcome)
        return
    }
    Column(modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalAlignment = Alignment.End) {
        Surface(
            shape = RoundedCornerShape(topStart = 24.dp, topEnd = 6.dp, bottomEnd = 24.dp, bottomStart = 24.dp),
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
            modifier = Modifier.widthIn(max = 480.dp).fillMaxWidth(0.82f).wrapContentWidth(Alignment.End),
        ) {
            Text(message.text, modifier = Modifier.padding(horizontal = 18.dp, vertical = 12.dp),
                style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurface)
        }
    }
}

@Composable
private fun ApprovalCard(
    approval: ApprovalView,
    modifier: Modifier = Modifier,
    deadlineMillis: Long,
    otp: String,
    busy: Boolean,
    canPromptBiometric: Boolean,
    onOtpChange: (String) -> Unit,
    onRequestOtp: () -> Unit,
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
    BankSectionCard(modifier = modifier, containerColor = if (isRed) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.tertiaryContainer) {
        Column(verticalArrangement = Arrangement.spacedBy(BankSpacing.sm)) {
            BankStatusChip(if (isRed) "红 · 强验证" else "黄 · 需确认", if (isRed) BankSemanticStatus.RiskRed else BankSemanticStatus.RiskYellow,
                Modifier.breathe(active = remainingSeconds > 0, min = 0.6f))
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
                    text = if (isRed) "验证身份后执行" else "请核对本次操作",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurface,
                )
            }
            Text(
                approval.summary,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
            )
            Surface(
                color = MaterialTheme.colorScheme.surface.copy(alpha = 0.7f),
                shape = CircleShape,
            ) {
                Text(
                    text = if (remainingSeconds > 0) "请在 $remainingSeconds 秒内确认" else "已超时，请重新发起操作",
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
                )
            }
            // The remaining time drains smoothly and turns red in the last 15 seconds.
            val total = approval.expiresIn.coerceAtLeast(1).toFloat()
            val fraction by animateFloatAsState((remainingSeconds / total).coerceIn(0f, 1f), tween(450, easing = LinearEasing), label = "approvalTime")
            LinearProgressIndicator(
                progress = { fraction },
                modifier = Modifier.fillMaxWidth().height(4.dp).clip(CircleShape),
                color = if (remainingSeconds <= 15) riskColors.riskRed else MaterialTheme.colorScheme.primary,
                trackColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.6f),
                drawStopIndicator = {},
            )
            if (isRed) {
                OutlinedTextField(
                    colors = bankTextFieldColors(),
                    value = otp,
                    onValueChange = onOtpChange,
                    label = { Text("验证码") },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.APPROVAL_OTP),
                )
                TextButton(onClick = onRequestOtp, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.onSurface), enabled = !busy && remainingSeconds > 0) { Text("获取本机一次性演示验证码") }
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
                    text = if (isRed) "验证并确认办理" else "确认本次操作",
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
                "请核对办理对象、金额和具体内容。确认后，才会继续本次操作。",
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
                BankTextButton(onClick = onGiveUp, enabled = !busy) { Text("放弃") }
            }
            val quizProgress by animateFloatAsState(quiz.answered.toFloat() / quiz.questions.size, tween(400), label = "quizProgress")
            LinearProgressIndicator(
                progress = { quizProgress },
                modifier = Modifier.fillMaxWidth().height(6.dp).clip(CircleShape),
                drawStopIndicator = {},
            )
            key(question.number) {
            Column(Modifier.riseIn(fromX = 24.dp, rise = 0.dp, durationMillis = 300), verticalArrangement = Arrangement.spacedBy(BankSpacing.xs)) {
            Text(question.text, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Bold)
            question.options.forEach { option ->
                Surface(
                    onClick = { onAnswer(question, option.key) },
                    enabled = !busy,
                    shape = RoundedCornerShape(16.dp),
                    color = MaterialTheme.colorScheme.surface,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                ) {
                    Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            option.key,
                            style = MaterialTheme.typography.titleSmall,
                            color = MaterialTheme.colorScheme.secondary,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.width(24.dp),
                        )
                        Text(option.label, style = MaterialTheme.typography.bodyMedium)
                    }
                }
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
    voiceEnabled: Boolean,
    captureVoiceResult: () -> (String) -> Unit,
    cloudSpeechAvailable: ((Boolean) -> Unit) -> Unit,
    transcribeCloud: (ByteArray, (String) -> Unit, (String) -> Unit) -> Unit,
    onValueChange: (String) -> Unit,
    onSend: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val focus = LocalFocusManager.current
    val send = { if (enabled) { focus.clearFocus(); onSend() } }
    Column(modifier.fillMaxWidth().background(MaterialTheme.colorScheme.background).padding(start = 12.dp, end = 12.dp, top = 6.dp, bottom = 8.dp)) {
        // One rounded composer holding the text, the microphone and the send button.
        Surface(shape = RoundedCornerShape(28.dp), color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth()) {
            Row(Modifier.fillMaxWidth().padding(start = 8.dp, end = 8.dp, top = 4.dp, bottom = 4.dp),
                verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TextField(
                    value = value, onValueChange = onValueChange,
                    placeholder = { Text("问问安言", style = MaterialTheme.typography.bodyLarge) },
                    textStyle = MaterialTheme.typography.bodyLarge,
                    colors = TextFieldDefaults.colors(
                        focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
                        disabledContainerColor = Color.Transparent,
                        focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent,
                        disabledIndicatorColor = Color.Transparent,
                        cursorColor = LocalBankSemanticColors.current.inputFocus,
                    ),
                    modifier = Modifier.weight(1f).testTag(UiTags.CHAT_INPUT), maxLines = 5,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { send() }),
                )
                Row(Modifier.padding(bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    VoiceInputButton(enabled = voiceEnabled, captureResult = captureVoiceResult,
                        cloudAvailable = cloudSpeechAvailable, transcribeCloud = transcribeCloud)
                    FilledIconButton(
                        onClick = send, enabled = enabled,
                        colors = IconButtonDefaults.filledIconButtonColors(
                            containerColor = MaterialTheme.colorScheme.primary,
                            contentColor = MaterialTheme.colorScheme.onPrimary,
                            disabledContainerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
                            disabledContentColor = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                        ),
                        modifier = Modifier.size(48.dp).popOnChange(enabled).testTag(UiTags.CHAT_SEND),
                    ) {
                        BankIcon(BankIconKind.ArrowUp, Modifier.size(22.dp), contentDescription = "发送")
                    }
                }
            }
        }
        Text("安言可能会出错；资金操作以确认卡和银行核心回执为准。", style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth().padding(top = 6.dp))
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
    BankBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
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
                        BankTextButton(onClick = onUnbindFeishu, enabled = !feishuBusy) { Text("解除飞书绑定") }
                    }
                }
            }
            if (busy) item { BankLoadingState(message = "正在更新设备…") }
            if (!busy && devices.isEmpty()) item { BankEmptyState("没有已注册的设备。") }
            items(devices, key = { it.deviceId }) { device ->
                BankSectionCard(modifier = Modifier.animateItem()) {
                    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            BankIcon(BankIconKind.Shield, Modifier.size(28.dp), tint = MaterialTheme.colorScheme.secondary)
                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Text(device.label ?: "未知设备", style = MaterialTheme.typography.titleMedium)
                            }
                            BankStatusChip(if (device.status == "ACTIVE") "已启用" else "已停用",
                                if (device.status == "ACTIVE") BankSemanticStatus.RiskGreen else BankSemanticStatus.Neutral)
                        }
                        if (device.createdAt > 0) Text("注册于 ${formatEpochSeconds(device.createdAt)}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        var showDetails by rememberSaveable(device.deviceId) { mutableStateOf(false) }
                        BankTextButton(onClick = { showDetails = !showDetails }, contentPadding = PaddingValues(0.dp)) {
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
    onTakeover: (String, String) -> Unit,
    onPaySplit: (SplitRequestView) -> Unit,
    onDeclineSplit: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var takeoverId by remember { mutableStateOf<String?>(null) }
    var takeoverReason by remember { mutableStateOf("") }
    if (takeoverId != null) {
        AlertDialog(
            onDismissRequest = { takeoverId = null },
            title = { Text("请求人工处理") },
            text = { Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("将暂停本任务及关联的待执行步骤。已完成操作不会撤销，已锁定资金不会自动释放。本机演示没有在线客服。")
                OutlinedTextField(value = takeoverReason, onValueChange = { takeoverReason = it.take(300) },
                    label = { Text("处理原因") }, modifier = Modifier.fillMaxWidth(), maxLines = 4)
            } },
            confirmButton = { TextButton(colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.onSurface), enabled = !busy && takeoverReason.isNotBlank(), onClick = {
                takeoverId?.let { onTakeover(it, takeoverReason) }
                takeoverId = null
            }) { Text("确认暂停") } },
            dismissButton = { TextButton(colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.onSurface), onClick = { takeoverId = null }) { Text("返回") } },
        )
    }
    // The sheet window never takes focus, so system Back reaches the Activity
    // dispatcher and would finish the app instead of closing the panel.
    BackHandler(enabled = true) { onDismiss() }
    BankBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
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
                            BankIcon(BankIconKind.Check, Modifier.size(26.dp), tint = MaterialTheme.colorScheme.secondary)
                            Text(taskTypeLabel(task.taskType), style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                            BankStatusChip(taskStatusLabel(task.status), when (task.status) {
                                "COMPLETED", "SUCCESS", "SUCCEEDED" -> BankSemanticStatus.RiskGreen
                                "FAILED" -> BankSemanticStatus.RiskRed
                                "WAITING_USER" -> BankSemanticStatus.RiskYellow
                                else -> BankSemanticStatus.Neutral
                            })
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
                            BankSecondaryButton("取消", { onCancel(task.taskId) }, enabled = !busy,
                                modifier = Modifier.fillMaxWidth().testTag(UiTags.TASK_CANCEL))
                            BankSecondaryButton("请求人工处理", { takeoverId = task.taskId; takeoverReason = "" }, enabled = !busy,
                                modifier = Modifier.fillMaxWidth().testTag("task_takeover"))
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
