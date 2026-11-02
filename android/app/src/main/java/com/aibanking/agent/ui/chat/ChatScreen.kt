package com.aibanking.agent.ui.chat

import android.os.SystemClock

import androidx.activity.compose.BackHandler
import androidx.compose.animation.*
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.zIndex
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.fragment.app.FragmentActivity
import com.aibanking.agent.data.DeviceView
import com.aibanking.agent.data.SplitRequestView
import com.aibanking.agent.data.TaskView
import com.aibanking.agent.security.BiometricGate
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.BankConversationMotion
import com.aibanking.agent.ui.anim.BankHaptics
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.anim.GradientRevealText
import com.aibanking.agent.ui.anim.PredictiveBackPreview
import com.aibanking.agent.ui.anim.RiskSignal
import com.aibanking.agent.ui.anim.RiskTier
import com.aibanking.agent.ui.anim.ShimmerGradientText
import com.aibanking.agent.ui.anim.bankPressScale
import com.aibanking.agent.ui.anim.pageSlideSpec
import com.aibanking.agent.ui.anim.rememberEntranceTracker
import com.aibanking.agent.ui.anim.riskEdgeGlow
import com.aibanking.agent.ui.anim.riseIn
import com.aibanking.agent.ui.anim.verificationFrost
import com.aibanking.agent.ui.components.*
import com.aibanking.agent.ui.theme.BankSpacing
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** 聊天之外的四个全屏覆盖页；聊天页是层级最浅的主页。 */
private enum class AppPage { CHAT, HISTORY, CARDS, WEALTH, CONTACTS }

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun ChatScreen(viewModel: ChatViewModel, activity: FragmentActivity) {
    val state by viewModel.state.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    val listState = rememberLazyListState()
    ChatMessageEffects(state.error, state.notice, snackbar, viewModel::consumeMessages, owner = state.principal)
    // The deadline survives destination changes and Activity recreation.
    val approvalDeadlineMillis = rememberSaveable(state.pending?.approvalId) {
        state.pending?.let { SystemClock.elapsedRealtime() + it.expiresIn.coerceAtLeast(0) * 1_000L } ?: 0L
    }
    // Keep the deadline and notifications alive while viewing a receipt.
    // Leaving their composition would restart the approval clock on return.
    val resultMessage = state.messages.firstOrNull { it.id == state.outcomeMessageId }
    val outcome = resultMessage?.let(::outcomeFor) ?: state.lastProblem?.takeIf { state.showProblem }?.let(::outcomeFor)
    if (outcome != null) {
        OperationOutcomeScreen(
            outcome, resultMessage, state.lastProblem.takeIf { state.showProblem },
            canQuery = state.authenticated && !state.busy && state.pending == null && state.quiz == null,
            onBack = viewModel::closeOutcome, onBalance = viewModel::outcomeBalance, onTasks = viewModel::outcomeTasks,
            onReconcile = { resultMessage?.let { viewModel.reconcileMessage(it.id) } }, snackbarHostState = snackbar,
        )
        return
    }

    // 页面状态映射
    val page = when {
        state.showHistory -> AppPage.HISTORY
        state.showCards -> AppPage.CARDS
        state.showWealth -> AppPage.WEALTH
        state.showContacts -> AppPage.CONTACTS
        else -> AppPage.CHAT
    }

    var drawerOpen by rememberSaveable { mutableStateOf(false) }
    val motion = LocalBankMotionEnabled.current
    val drawerProgress by animateFloatAsState(
        targetValue = if (drawerOpen) 1f else 0f,
        animationSpec = if (motion) tween(durationMillis = 320, easing = FastOutSlowInEasing) else snap(),
        label = "zoomDrawerProgress",
    )

    fun openDrawer() { drawerOpen = true }
    fun closeDrawer() { drawerOpen = false }
    fun closeDrawerThen(action: () -> Unit) {
        drawerOpen = false
        action()
    }
    BackHandler(enabled = drawerOpen) { closeDrawer() }

    val riskSignal = activeApprovalRiskSignal(state.pending, state.approvalConfirmed)

    val drawerWidth = 280.dp
    val shiftDistance = 250.dp
    val targetScale = 1f - 0.18f * drawerProgress
    val cornerRadius = 24.dp * drawerProgress
    val elevation = 16.dp * drawerProgress

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .riskEdgeGlow(riskSignal),
    ) {
        // 1. 左侧抽屉面板：伴随抽屉打开从左侧平滑滑入
        Box(
            modifier = Modifier
                .fillMaxHeight()
                .width(drawerWidth)
                .graphicsLayer {
                    translationX = -drawerWidth.toPx() * 0.35f * (1f - drawerProgress)
                    alpha = drawerProgress.coerceIn(0f, 1f)
                }
                .zIndex(0f),
        ) {
            BankNavigationDrawer(
                open = drawerOpen,
                currentPage = page,
                principal = state.principal,
                onChat = { closeDrawerThen(viewModel::dismissPanels) },
                onHistory = { closeDrawerThen(viewModel::openHistory) },
                onCards = { closeDrawerThen(viewModel::openCards) },
                onWealth = { closeDrawerThen(viewModel::openWealth) },
                onContacts = { closeDrawerThen(viewModel::openContacts) },
                onTasks = { closeDrawerThen(viewModel::openTasks) },
                onDevices = { closeDrawerThen(viewModel::openDevices) },
                onLogout = { closeDrawerThen(viewModel::logout) },
            )
        }

        // 2. 主页面：右移 + 缩小（缩放至 0.82，附带圆角与阴影）
        Box(
            modifier = Modifier
                .fillMaxSize()
                .graphicsLayer {
                    scaleX = targetScale
                    scaleY = targetScale
                    translationX = shiftDistance.toPx() * drawerProgress
                    shape = RoundedCornerShape(cornerRadius)
                    clip = drawerProgress > 0.01f
                    shadowElevation = elevation.toPx()
                }
                .zIndex(1f),
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
                                    AppPage.CHAT -> ChatHome(
                                        state, viewModel, activity, listState, approvalDeadlineMillis,
                                        onMenu = ::openDrawer,
                                    )
                                    AppPage.HISTORY -> HistoryUi(state, viewModel)
                                    AppPage.CARDS -> CardsUi(state, viewModel)
                                    AppPage.WEALTH -> WealthUi(state, viewModel, snackbar)
                                    AppPage.CONTACTS -> ContactsUi(state, viewModel)
                                }
                            }
                        }
                    }
                }
            }

            // 3. 点击主页面复原蒙层：抽屉展开后覆盖在缩小的页面上，点击即可触发复原
            if (drawerProgress > 0.01f) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .clip(RoundedCornerShape(cornerRadius))
                        .background(Color.Black.copy(alpha = 0.08f * drawerProgress))
                        .clickable(
                            interactionSource = remember { MutableInteractionSource() },
                            indication = null,
                            onClickLabel = "复原主页面",
                            onClick = ::closeDrawer,
                        ),
                )
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
            submitting = state.taskSubmitting,
            onCancel = viewModel::cancelTask,
            onTakeover = viewModel::takeoverTask,
            error = state.panelError,
            onClearError = viewModel::clearPanelError,
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
    onHistory: () -> Unit,
    onCards: () -> Unit,
    onWealth: () -> Unit,
    onContacts: () -> Unit,
    onTasks: () -> Unit,
    onDevices: () -> Unit,
    onLogout: () -> Unit,
) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceContainerLow,
        shape = RoundedCornerShape(topEnd = 28.dp, bottomEnd = 28.dp),
        modifier = Modifier.fillMaxHeight().widthIn(max = 300.dp).testTag(UiTags.NAV_DRAWER),
    ) {
        Column(Modifier.fillMaxHeight().verticalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 16.dp)) {
            Row(
                Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                BankGradientMark(Modifier.size(28.dp))
                Text("安言", style = MaterialTheme.typography.titleLarge)
            }
            Spacer(Modifier.height(12.dp))
            val main = listOf(
                DrawerEntry("对话", BankIconKind.Chat, "open_assistant", AppPage.CHAT, onChat),
                DrawerEntry("办理历史", BankIconKind.History, UiTags.OPEN_HISTORY, AppPage.HISTORY, onHistory),
                DrawerEntry("卡包", BankIconKind.Card, UiTags.OPEN_CARDS, AppPage.CARDS, onCards),
                DrawerEntry("理财", BankIconKind.Chart, UiTags.OPEN_WEALTH, AppPage.WEALTH, onWealth),
                DrawerEntry("联系人", BankIconKind.People, UiTags.OPEN_CONTACTS, AppPage.CONTACTS, onContacts),
            )
            val tools = listOf(
                DrawerEntry("待办任务", BankIconKind.Task, UiTags.OPEN_TASKS, null, onTasks),
                DrawerEntry("设备与飞书", BankIconKind.Shield, UiTags.OPEN_DEVICES, null, onDevices),
            )
            main.forEachIndexed { index, entry -> DrawerItem(entry, selected = entry.page == currentPage, open = open, index = index) }
            Text(
                "服务",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 16.dp, top = 20.dp, bottom = 8.dp),
            )
            tools.forEachIndexed { index, entry -> DrawerItem(entry, selected = false, open = open, index = main.size + index) }
            Spacer(Modifier.height(24.dp))
            HorizontalDivider(Modifier.padding(horizontal = 12.dp, vertical = 12.dp), color = MaterialTheme.colorScheme.outlineVariant)
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                AccountAvatar(principal)
                Column(Modifier.weight(1f)) {
                    Text(principal.ifBlank { "已登录" }, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text("演示账户 · 本机模拟银行", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            DrawerItem(
                DrawerEntry("退出登录", BankIconKind.Logout, UiTags.LOGOUT, null, onLogout),
                selected = false,
                open = open,
                index = main.size + tools.size,
            )
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
            modifier = Modifier
                .heightIn(min = 52.dp)
                .testTag(entry.tag)
                .riseIn(animate = open, delayMillis = 40 + index * 35, durationMillis = 260, rise = 0.dp, fromX = (-16).dp),
        )
    }
}

/** Initial-letter avatar on the solid brand colour. */
@Composable
internal fun AccountAvatar(principal: String, modifier: Modifier = Modifier, onClick: (() -> Unit)? = null) {
    val initial = principal.removePrefix("user_").firstOrNull()?.uppercaseChar()?.toString() ?: "我"
    Box(
        modifier
            .size(36.dp)
            .clip(CircleShape)
            .background(LocalBankSemanticColors.current.brandGradient)
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClickLabel = "打开菜单", onClick = onClick) else Modifier),
        contentAlignment = Alignment.Center,
    ) {
        Text(initial, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onPrimary)
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
    val thinking = state.busy && state.messages.none { it.streaming } && (state.pending == null || state.activeStages.isNotEmpty())
    val itemCount = state.messages.size + (if (state.pending != null) 1 else 0) +
        (if (state.quiz != null) 1 else 0) + (if (showWelcome) 1 else 0) + (if (thinking) 1 else 0)

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
            ConversationList(
                listState = listState,
                contentRevision = listOf(state.messages.lastOrNull(), state.pending?.approvalId, state.quiz?.answered, thinking, state.activeStages),
                latestUserMessageId = state.messages.lastOrNull { it.role == ChatRole.USER }?.id,
                itemCount = itemCount,
                ensureLastCardVisible = state.pending != null || state.quiz != null,
                followUpdates = !showWelcome || state.pending != null,
                latestLabel = if (state.pending != null) "查看待确认操作" else "回到最新消息",
                modifier = Modifier.weight(1f).fillMaxWidth(),
            ) {
                if (showWelcome) {
                    item(key = "welcome") { ChatWelcome(onSuggestion = viewModel::onSuggestionClick) }
                }
                items(state.messages, key = { it.id }) { message ->
                    val fresh = remember(message.id) { entrance.isNew(message.id) }
                    val arrive = if (message.role == ChatRole.USER) {
                        Modifier.riseIn(
                            fresh,
                            rise = 6.dp,
                            fromX = 14.dp,
                            durationMillis = BankConversationMotion.ReplyEntranceMillis,
                        )
                    } else {
                        Modifier.riseIn(
                            fresh,
                            rise = 10.dp,
                            durationMillis = BankConversationMotion.ReplyEntranceMillis,
                        )
                    }
                    MessageBubble(
                        message,
                        Modifier.animateItem().then(arrive).verificationFrost(redVerification),
                        freshReveal = fresh,
                        onReconcile = { viewModel.reconcileMessage(message.id) },
                        onOutcome = { viewModel.openOutcome(message.id) },
                        onWealthProduct = viewModel::openWealthProduct,
                        onWealthBrowse = viewModel::openWealth,
                    )
                }
                state.pending?.let { approval ->
                    item(key = "approval:${approval.approvalId}") {
                        ApprovalCard(
                            modifier = Modifier.riseIn(rise = 0.dp, durationMillis = BankConversationMotion.FeedbackMillis),
                            approval = approval,
                            deadlineMillis = requireNotNull(approvalDeadlineMillis),
                            otp = state.otp,
                            busy = state.busy,
                            confirmed = state.approvalConfirmed,
                            error = state.approvalError,
                            onOtpChange = viewModel::onOtpChange,
                            onRequestOtp = viewModel::requestDemoOtp,
                            onReject = viewModel::reject,
                            onApprove = viewModel::approve,
                            onBiometricApprove = {
                                // Prove presence locally first; the server still
                                // verifies the device signature it receives.
                                viewModel.markApprovalConfirmed()
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
                if (thinking) {
                    item(key = "thinking") {
                        ThinkingReply(state.activeStages, Modifier.animateItem().riseIn(rise = 10.dp, durationMillis = 260))
                    }
                }
            }
            state.lastProblem?.let {
                BankSecondaryButton(
                    "查看原因与下一步",
                    viewModel::openProblem,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 4.dp).testTag(UiTags.OPEN_FAILURE),
                )
            }
            // Predicted next requests stay directly above the editor. A tap sends
            // immediately; write operations still enter the server approval flow.
            if (state.suggestions.isNotEmpty() && state.quiz == null && state.input.isBlank()) {
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
                                modifier = Modifier
                                    .testTag(UiTags.quickSuggestion(index))
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
                processing = state.busy,
                contacts = state.contacts,
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
            Text(
                "今天想办点什么？",
                style = MaterialTheme.typography.displaySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.55f),
                modifier = Modifier.riseIn(delayMillis = 450, rise = 10.dp, durationMillis = 500),
            )
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
                            Surface(
                                onClick = { onSuggestion(prompt) },
                                shape = RoundedCornerShape(20.dp),
                                color = MaterialTheme.colorScheme.surfaceContainer,
                                interactionSource = press,
                                modifier = Modifier
                                    .weight(1f)
                                    .riseIn(delayMillis = 600 + order * 80, rise = 20.dp, durationMillis = 420)
                                    .bankPressScale(press),
                            ) {
                                Column(Modifier.heightIn(min = 96.dp).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                                    Box(
                                        Modifier
                                            .size(32.dp)
                                            .clip(CircleShape)
                                            .background(if (readOnly) semantic.riskGreenContainer else MaterialTheme.colorScheme.primaryContainer),
                                        contentAlignment = Alignment.Center,
                                    ) {
                                        BankIcon(
                                            icon,
                                            Modifier.size(18.dp),
                                            tint = if (readOnly) semantic.onRiskContainer else MaterialTheme.colorScheme.onPrimaryContainer,
                                        )
                                    }
                                    Text(label, style = MaterialTheme.typography.titleSmall)
                                    Text(
                                        if (readOnly) "直接查询" else "补充信息后再确认",
                                        style = MaterialTheme.typography.labelMedium,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
        Column(Modifier.riseIn(delayMillis = 950, rise = 8.dp, durationMillis = 400), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(
                "查询直接回复，资金操作先确认；需要确认时，屏幕边缘会亮起对应颜色的光效。",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                BankStatusChip("绿 · 只读", BankSemanticStatus.RiskGreen)
                BankStatusChip("黄 · 需确认", BankSemanticStatus.RiskYellow)
                BankStatusChip("红 · 强验证", BankSemanticStatus.RiskRed)
            }
        }
    }
}

/** The assistant's place in the conversation while it works: its mark turns and the steps fill in. */
@Composable
internal fun ThinkingReply(stages: List<com.aibanking.agent.data.StageProgress>, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier.fillMaxWidth().padding(vertical = 6.dp).testTag("thinking_reply"),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(Modifier.padding(top = 2.dp)) { ThinkingMark() }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            ShimmerGradientText(
                "安言正在为您处理…",
                LocalBankSemanticColors.current.brandGradientColors,
                style = MaterialTheme.typography.bodyLarge,
            )
            if (stages.isNotEmpty()) ReplyPanel {
                Text("处理进度", style = MaterialTheme.typography.labelLarge)
                WorkflowProgress(stages)
                Text(
                    "处理步骤的完成状态以实际回执为准。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
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
    BankGradientMark(Modifier.size(26.dp).rotate(angle))
}

@Composable
private fun MessageBubble(
    message: ChatMessage,
    modifier: Modifier = Modifier,
    freshReveal: Boolean = false,
    onReconcile: () -> Unit = {},
    onOutcome: () -> Unit = {},
    onWealthProduct: (String) -> Unit = {},
    onWealthBrowse: () -> Unit = {},
) {
    if (message.role == ChatRole.ASSISTANT) {
        AgentReplyCard(message, modifier, freshReveal = freshReveal, onReconcile = onReconcile, onOutcome = onOutcome,
            onWealthProduct = onWealthProduct, onWealthBrowse = onWealthBrowse)
        return
    }
    Column(modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalAlignment = Alignment.End) {
        Surface(
            shape = RoundedCornerShape(topStart = 24.dp, topEnd = 6.dp, bottomEnd = 24.dp, bottomStart = 24.dp),
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
            modifier = Modifier.widthIn(max = 480.dp).fillMaxWidth(0.82f).wrapContentWidth(Alignment.End),
        ) {
            Text(
                message.text,
                modifier = Modifier.padding(horizontal = 18.dp, vertical = 12.dp),
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurface,
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
    val scope = rememberCoroutineScope()
    val view = LocalView.current
    val motion = com.aibanking.agent.ui.anim.LocalBankMotionEnabled.current
    // The tapped option lights up briefly before the next question slides in.
    var picked by remember(question.number) { mutableStateOf<String?>(null) }
    BankSectionCard(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = BankSpacing.sm, vertical = BankSpacing.xxs)
            .riseIn(rise = 20.dp, durationMillis = 360),
    ) {
        Column(Modifier.fillMaxWidth().animateContentSize(tween(260)), verticalArrangement = Arrangement.spacedBy(BankSpacing.sm)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("风险测评 · 第 ", style = MaterialTheme.typography.titleSmall)
                    AnimatedContent(
                        targetState = quiz.answered + 1,
                        transitionSpec = { (slideInVertically { it } + fadeIn()) togetherWith (slideOutVertically { -it } + fadeOut()) },
                        label = "quizNumber",
                    ) { number -> Text("$number", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold) }
                    Text(" / ${quiz.questions.size} 题", style = MaterialTheme.typography.titleSmall)
                }
                BankTextButton(onClick = onGiveUp, enabled = !busy) { Text("放弃") }
            }
            val quizProgress by animateFloatAsState(quiz.answered.toFloat() / quiz.questions.size, tween(420), label = "quizProgress")
            LinearProgressIndicator(
                progress = { quizProgress },
                modifier = Modifier.fillMaxWidth().height(6.dp).clip(CircleShape),
                drawStopIndicator = {},
            )
            AnimatedContent(
                targetState = question,
                transitionSpec = {
                    (slideInHorizontally(tween(320)) { it / 3 } + fadeIn(tween(320))) togetherWith
                        (slideOutHorizontally(tween(220)) { -it / 4 } + fadeOut(tween(180)))
                },
                contentKey = { it.number },
                label = "quizQuestion",
            ) { shown ->
                Column(verticalArrangement = Arrangement.spacedBy(BankSpacing.xs)) {
                    Text(shown.text, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold)
                    shown.options.forEachIndexed { index, option ->
                        val chosen = picked == option.key && shown.number == question.number
                        val container by animateColorAsState(
                            if (chosen) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface,
                            tween(180),
                            label = "optionColor",
                        )
                        Surface(
                            onClick = {
                                if (picked == null) {
                                    BankHaptics.confirm(view)
                                    picked = option.key
                                    scope.launch {
                                        if (motion) delay(240)
                                        onAnswer(shown, option.key)
                                    }
                                }
                            },
                            enabled = !busy && picked == null,
                            shape = RoundedCornerShape(16.dp),
                            color = container,
                            modifier = Modifier
                                .fillMaxWidth()
                                .heightIn(min = 48.dp)
                                .riseIn(delayMillis = 60 + index * 40, rise = 8.dp, durationMillis = 240),
                        ) {
                            Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    option.key,
                                    style = MaterialTheme.typography.titleSmall,
                                    color = MaterialTheme.colorScheme.secondary,
                                    fontWeight = FontWeight.Bold,
                                    modifier = Modifier.width(24.dp),
                                )
                                Text(option.label, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                                AnimatedVisibility(chosen, enter = scaleIn() + fadeIn(), exit = fadeOut()) {
                                    BankIcon(BankIconKind.Check, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onPrimaryContainer)
                                }
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
    BankBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = false),
    ) {
        LazyColumn(
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            item {
                BankScreenHeading("已注册设备", subtitle = "管理可访问您账户的设备", eyebrow = "设备安全")
            }
            item {
                BankSectionCard {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("飞书私聊查询", style = MaterialTheme.typography.titleMedium)
                        Text(
                            "获取一次性绑定码后，在飞书机器人私聊发送“/绑定 绑定码”。余额与账单可在飞书查询；转账和审批仍需回到本应用。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
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
                            BankStatusChip(
                                if (device.status == "ACTIVE") "已启用" else "已停用",
                                if (device.status == "ACTIVE") BankSemanticStatus.RiskGreen else BankSemanticStatus.Neutral,
                            )
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
                            BankSecondaryButton(
                                "停用",
                                { onRevoke(device.deviceId) },
                                enabled = !busy,
                                modifier = Modifier.fillMaxWidth().testTag(UiTags.DEVICE_REVOKE),
                            )
                        }
                    }
                }
            }
        }
    }
}

private fun formatEpochSeconds(epochSeconds: Long): String =
    runCatching {
        DateTimeFormatter.ofPattern("yyyy-MM-dd")
            .withZone(ZoneId.systemDefault())
            .format(Instant.ofEpochSecond(epochSeconds))
    }.getOrDefault("")
