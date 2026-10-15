package com.aibanking.agent.ui.chat

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.aibanking.agent.data.CardRevealView
import com.aibanking.agent.data.CardView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.ShimmerSwitch
import com.aibanking.agent.ui.anim.SkeletonBlock
import com.aibanking.agent.ui.anim.cascadeItem
import com.aibanking.agent.ui.anim.pageSlideSpec
import com.aibanking.agent.ui.anim.rememberCascadeEntrance
import com.aibanking.agent.ui.components.BankBottomSheet
import com.aibanking.agent.ui.components.BankButton
import com.aibanking.agent.ui.components.BankCard
import com.aibanking.agent.ui.components.BankFilterChip
import com.aibanking.agent.ui.components.BankIcon
import com.aibanking.agent.ui.components.BankIconKind
import com.aibanking.agent.ui.components.BankOutlinedButton
import com.aibanking.agent.ui.components.BankScreenHeading
import com.aibanking.agent.ui.components.BankSemanticStatus
import com.aibanking.agent.ui.components.BankStatusChip
import com.aibanking.agent.ui.components.BankSticker
import com.aibanking.agent.ui.components.BankTextButton
import com.aibanking.agent.ui.components.bankHardShadow
import com.aibanking.agent.ui.components.bankTextFieldColors
import com.aibanking.agent.ui.theme.BankPalette
import com.aibanking.agent.ui.theme.BankSpacing
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import java.util.Locale
import kotlinx.coroutines.launch
import kotlinx.coroutines.Job

/**
 * 银行风格“我的银行卡”页：仿真卡面（渐变卡板 + 脱敏卡号 + 余额）+ 卡片
 * 操作 + 自行添加银行卡。
 *
 * 余额与所属账户共享（借记卡语义）；冻结/挂失属于红色风险操作，页面把
 * 话术预填进聊天，由核心的短信 + 设备签名审批流最终授权。添加卡片为
 * 黄色操作，需用户明确确认后提交并审计。
 */

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CardsUi(state: ChatUiState, viewModel: ChatViewModel) {
    var showAdd by remember { mutableStateOf(false) }
    var selectedCard by remember { mutableStateOf<CardView?>(null) }
    // 打开表单那一刻的卡片数量。绑定是异步的（viewModelScope.launch），
    // 只有列表真的多出一张卡才收起表单。
    var cardsBeforeSubmit by remember { mutableStateOf<Int?>(null) }
    // 提交成功 → cards 变长 → 收起表单；失败则原地保留用户输入与错误提示，
    // 不再像以前那样先关表单再发请求（失败时输入被吞掉，只剩一条 Snackbar）。
    LaunchedEffect(showAdd, state.cards.size) {
        val submittedAt = cardsBeforeSubmit ?: return@LaunchedEffect
        if (state.cards.size > submittedAt) {
            showAdd = false
            cardsBeforeSubmit = null
        }
    }
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Text("卡片管家", fontWeight = FontWeight.Bold)
                        Surface(
                            shape = MaterialTheme.shapes.small,
                            color = MaterialTheme.colorScheme.primaryContainer,
                        ) {
                            Text(
                                "${state.cards.size} 张",
                                style = MaterialTheme.typography.labelMedium,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.secondary,
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                            )
                        }
                    }
                },
                navigationIcon = {
                    IconButton(onClick = viewModel::closeCards) {
                        Icon(Icons.Default.Close, contentDescription = "返回聊天")
                    }
                },
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = { showAdd = true },
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.bankHardShadow(RoundedCornerShape(12.dp)).border(BankPalette.borderWidth, MaterialTheme.colorScheme.outline, RoundedCornerShape(12.dp)).testTag(UiTags.CARD_OPEN_ADD),
                icon = { Icon(Icons.Default.Add, contentDescription = null) },
                text = { Text("添加银行卡") },
            )
        },
    ) { padding ->
        ShimmerSwitch(
            loading = state.panelBusy && state.cards.isEmpty(),
            modifier = Modifier.fillMaxSize().padding(padding),
            skeleton = { CardsSkeleton() },
        ) {
            val cascade = rememberCascadeEntrance(ready = state.cards.isNotEmpty())
            LazyColumn(
                modifier = Modifier.fillMaxSize().testTag(UiTags.CARD_LIST),
                contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 12.dp, bottom = 104.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
            ) {
                item {
                    BankScreenHeading("每张卡，都心中有数", subtitle = "管理卡片、限额与安全状态", eyebrow = "卡片管理")
                }
                items(
                    count = state.cards.size,
                    key = { state.cards[it].cardId },
                ) { index ->
                    BankCardVisual(state.cards[index], viewModel, Modifier.cascadeItem(cascade, index)) { selectedCard = state.cards[index] }
                }
                if (state.cards.isEmpty() && !state.panelBusy) {
                    item {
                        val loadFailure = state.panelError
                        if (loadFailure != null) {
                            PanelLoadFailure(loadFailure, viewModel::refreshCards)
                        } else {
                            BankCard(
                                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                                shape = RoundedCornerShape(16.dp),
                            ) {
                                Column(Modifier.fillMaxWidth().padding(28.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                                    BankIcon(BankIconKind.Card, Modifier.size(40.dp), tint = MaterialTheme.colorScheme.secondary)
                                    Text("把常用的卡放在一起", style = MaterialTheme.typography.titleLarge)
                                    Text("还没有银行卡。点右下角即可自行添加。", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        }
                    }
                }
                item {
                    Text(
                        "银行卡余额与所属账户共享。冻结、挂失等操作将在对话中完成安全验证。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.cascadeItem(cascade, state.cards.size),
                    )
                }
            }
        }
    }
    if (showAdd) {
        AddCardSheet(
            visible = true,
            busy = state.panelBusy,
            cardType = state.cardType,
            label = state.cardLabel,
            onFieldChange = viewModel::onCardFormChange,
            onAdd = { number, source ->
                if (!state.panelBusy) {
                    cardsBeforeSubmit = state.cards.size
                    viewModel.addCard(number, source)
                }
            },
            onDismiss = {
                showAdd = false
                cardsBeforeSubmit = null
            },
        )
    }
    AnimatedContent(
        targetState = selectedCard,
        transitionSpec = { pageSlideSpec { if (it == null) 0 else 1 } },
        modifier = Modifier.fillMaxSize(),
        label = "cardDetail",
    ) { card ->
        if (card != null) CardDetail(
            card = card,
            revealCard = viewModel::revealCard,
            onTransfer = { viewModel.prefillCardTransfer(card) },
            onWealth = viewModel::openWealth,
            onBack = { selectedCard = null },
        )
    }
}

/** 加载中的卡面骨架：与真实卡片排版一致的占位块（含高光扫过）。 */
@Composable
private fun CardsSkeleton() {
    Column(
        Modifier.fillMaxSize().padding(BankSpacing.md),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        repeat(3) {
            SkeletonBlock(height = 280.dp, corner = 24.dp)
        }
    }
}

@Composable
private fun BankCardVisual(card: CardView, viewModel: ChatViewModel, modifier: Modifier = Modifier, onOpen: () -> Unit = {}) {
    BankCard(
        modifier = modifier.fillMaxWidth().testTag("${UiTags.CARD_DETAIL}:${card.cardId}").clickable(onClick = onOpen),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = BorderStroke(BankPalette.borderWidth, MaterialTheme.colorScheme.outline),
    ) {
        CardFace(card)
        Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                CardLimitMetric("每日限额", "¥%,.0f".format(Locale.CHINA, card.dailyLimit), Modifier.weight(1f))
                CardLimitMetric("今日已用", "¥%,.2f".format(Locale.CHINA, card.dailySpentToday), Modifier.weight(1f))
            }
            card.dailyRemaining?.let {
                Text("今日剩余 ¥%,.2f".format(Locale.CHINA, it), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                cardActions(card.status).forEach { action ->
                    ActionChip(actionLabel(action), card, action) { viewModel.startCardAction(action, card) }
                }
                Spacer(Modifier.weight(1f))
                Icon(Icons.AutoMirrored.Filled.ArrowForward, "查看卡片详情", tint = MaterialTheme.colorScheme.secondary, modifier = Modifier.size(20.dp))
            }
        }
    }
}

@Composable
private fun CardFace(card: CardView) {
    val semantic = LocalBankSemanticColors.current
    val base = when (card.status) {
        "LOST" -> semantic.cardLost
        "FROZEN" -> semantic.cardFrozen
        else -> if (card.type.contains("虚拟") || card.type.contains("VIRTUAL", ignoreCase = true)) semantic.cardVirtual else semantic.cardActive
    }
    val foreground = if (base == BankPalette.Ink) Color.White else BankPalette.Ink
    Box(
        Modifier.fillMaxWidth()
            .background(base)
            .padding(24.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
            BankSticker("ANYAN · BANKING")
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Column(Modifier.weight(1f)) {
                    Text("安言 AI 银行", color = foreground, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Text(card.type + (card.label?.takeIf { it.isNotBlank() }?.let { " · $it" } ?: ""), color = foreground.copy(alpha = .72f), style = MaterialTheme.typography.labelMedium)
                }
                StatusBadge(card.status)
            }
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("可用余额", color = foreground.copy(alpha = .72f), style = MaterialTheme.typography.labelMedium)
                Text("¥" + String.format(Locale.CHINA, "%,.2f", card.availableBalance), color = foreground, style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.SemiBold)
            }
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween) {
                Text("尾号 ${card.last4.ifBlank { card.numberMasked.takeLast(4) }}", color = foreground.copy(alpha = .9f), style = MaterialTheme.typography.titleSmall, fontFamily = FontFamily.Monospace, letterSpacing = 2.sp)
                Box(
                    Modifier.size(width = 34.dp, height = 26.dp)
                        .clip(RoundedCornerShape(7.dp))
                        .background(BankPalette.Yellow).border(BankPalette.borderWidth, BankPalette.Ink, RoundedCornerShape(7.dp)),
                    contentAlignment = Alignment.Center,
                ) {
                    BankIcon(BankIconKind.Card, Modifier.size(22.dp), tint = BankPalette.Ink)
                }
            }
        }
    }
}

@Composable
private fun CardLimitMetric(label: String, value: String, modifier: Modifier = Modifier) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
    }
}

/**
 * 卡片状态 → 可执行的操作，与核心的 `CARD_TRANSITIONS` 一一对应。
 *
 * 旧版对任何非 ACTIVE 的卡都只给“解冻”，而核心只允许 FROZEN → ACTIVE：挂失卡
 * 点下去必然被拒绝，用户卡在死路上。挂失的唯一出口是“解挂”（LOST → ACTIVE）。
 */
private fun cardActions(status: String): List<String> = when (status) {
    "ACTIVE" -> listOf("freeze", "report_lost")
    "FROZEN" -> listOf("unfreeze")
    "LOST" -> listOf("unreport_lost")
    else -> emptyList()
}

private fun actionLabel(action: String): String = when (action) {
    "freeze" -> "冻结"
    "unfreeze" -> "解冻"
    "report_lost" -> "挂失"
    "unreport_lost" -> "解挂"
    else -> action
}

@Composable
private fun StatusBadge(status: String) {
    val (label, semantic) = when (status) {
        "ACTIVE" -> "正常" to BankSemanticStatus.RiskGreen
        "FROZEN" -> "已冻结" to BankSemanticStatus.RiskYellow
        "LOST" -> "已挂失" to BankSemanticStatus.RiskRed
        else -> status to BankSemanticStatus.Neutral
    }
    BankStatusChip(
        label = label,
        status = semantic,
    )
}

@Composable
private fun ActionChip(text: String, card: CardView, action: String, onClick: () -> Unit) {
    BankOutlinedButton(onClick = onClick, modifier = Modifier.heightIn(min = BankSpacing.minTouchTarget).testTag(UiTags.cardAction(card.cardId, action))) {
        Text(text)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AddCardSheet(
    visible: Boolean,
    busy: Boolean,
    cardType: String,
    label: String,
    onFieldChange: (String?, String?) -> Unit,
    onAdd: (String, String) -> Unit,
    onDismiss: () -> Unit,
) {
    if (!visible) return
    // 弹层窗口不接收焦点，系统返回会落到 Activity 上直接退出应用；这里接管返回，
    // 并与 onDismissRequest 共用 busy 保护：提交进行中不允许返回把表单丢掉。
    BackHandler(enabled = true) { if (!busy) onDismiss() }
    BankBottomSheet(
        onDismissRequest = { if (!busy) onDismiss() },
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        var number by rememberSaveable { mutableStateOf("") }
        var source by rememberSaveable { mutableStateOf("MANUAL") }
        var confirmed by rememberSaveable { mutableStateOf(false) }
        var error by remember { mutableStateOf<String?>(null) }
        val context = LocalContext.current
        val scope = rememberCoroutineScope()
        val camera = rememberLauncherForActivityResult(ActivityResultContracts.TakePicturePreview()) { bitmap ->
            if (bitmap == null) return@rememberLauncherForActivityResult
            scope.launch {
                val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
                recognizer.process(InputImage.fromBitmap(bitmap, 0)).addOnSuccessListener { result ->
                    val found = extractCardNumber(result.text)
                    if (found != null) { number = found; source = "OCR"; confirmed = false }
                    else error = "未识别到有效卡号，请手动输入"
                }.addOnFailureListener { error = "拍照识别失败，请手动输入" }.addOnCompleteListener { recognizer.close() }
            }
        }
        val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) camera.launch(null) else error = "未获得相机权限，可手动输入卡号"
        }
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp)
                .padding(bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text("添加银行卡", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
            Text("输入卡号，或拍照识别后核对信息。", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            OutlinedTextField(
                colors = bankTextFieldColors(),
                value = number,
                onValueChange = { number = it; source = "MANUAL"; confirmed = false; error = null },
                label = { Text("卡号（必填）") },
                enabled = !busy,
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth().testTag(UiTags.CARD_NUMBER),
            )
            BankOutlinedButton(
                onClick = { if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) camera.launch(null) else permission.launch(Manifest.permission.CAMERA) },
                enabled = !busy,
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag(UiTags.CARD_CAMERA),
            ) {
                BankIcon(BankIconKind.Scan, Modifier.size(18.dp))
                Text("拍照", Modifier.padding(start = 8.dp))
            }
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = confirmed, onCheckedChange = { confirmed = it }, enabled = !busy, modifier = Modifier.testTag(UiTags.CARD_CONFIRM))
                Text("我确认卡号属于本人且输入无误", style = MaterialTheme.typography.bodySmall)
            }
            error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            Text("卡片类型", style = MaterialTheme.typography.labelLarge)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("借记卡", "虚拟卡").forEach { option ->
                    BankFilterChip(
                        selected = cardType == option,
                        onClick = { onFieldChange(option, null) },
                        enabled = !busy,
                        label = { Text(option) },
                    )
                }
            }
            OutlinedTextField(
                colors = bankTextFieldColors(),
                value = label,
                onValueChange = { onFieldChange(null, it) },
                label = { Text("备注（可选，例如：工资卡）") },
                singleLine = true,
                enabled = !busy,
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth(),
            )
            BankButton(
                onClick = { onAdd(number, source) },
                enabled = !busy && confirmed && extractCardNumber(number) != null,
                modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp).testTag(UiTags.CARD_ADD),
            ) { Text(if (busy) "绑定中…" else "确认添加") }
            Text(
                "开立即生效并计入审计；卡号为虚构演示号段，仅显示脱敏形式。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private fun CardNumberLuhn(number: String): Boolean = number.length in 12..19 && number.all(Char::isDigit) && number.reversed().map(Char::digitToInt).mapIndexed { i, d -> if (i % 2 == 1) if (d * 2 > 9) d * 2 - 9 else d * 2 else d }.sum() % 10 == 0

internal fun extractCardNumber(text: String): String? = Regex("(?<!\\d)(?:\\d[ -]?){12,19}(?!\\d)").findAll(text).map { it.value.filter(Char::isDigit) }.firstOrNull { CardNumberLuhn(it) }

@Composable
@OptIn(ExperimentalMaterial3Api::class)
internal fun CardDetail(
    card: CardView,
    revealCard: suspend (String, String) -> CardRevealView,
    onTransfer: () -> Unit,
    onWealth: () -> Unit,
    onBack: () -> Unit,
) {
    var pin by remember(card.cardId) { mutableStateOf("") }
    var reveal by remember(card.cardId) { mutableStateOf<CardRevealView?>(null) }
    var error by remember(card.cardId) { mutableStateOf<String?>(null) }
    var verifying by remember(card.cardId) { mutableStateOf(false) }
    var revealGeneration by remember(card.cardId) { mutableIntStateOf(0) }
    var revealJob by remember(card.cardId) { mutableStateOf<Job?>(null) }
    val scope = rememberCoroutineScope()
    fun hideSensitiveData() {
        // Cancellation alone cannot reject a non-cooperative late response, or
        // one arriving after the user has already returned to this screen.
        revealGeneration++
        revealJob?.cancel()
        revealJob = null
        reveal = null
        pin = ""
        error = null
        verifying = false
    }
    LaunchedEffect(reveal?.disclosureId) {
        val shown = reveal ?: return@LaunchedEffect
        val fallback = 30_000L
        val remaining = runCatching {
            val expiry = java.time.Instant.parse(shown.expiresAt).toEpochMilli()
            (expiry - System.currentTimeMillis()).coerceAtLeast(0L)
        }.getOrDefault(fallback)
        kotlinx.coroutines.delay(minOf(fallback, remaining))
        reveal = null
    }
    val lifecycleOwner = LocalLifecycleOwner.current
    val dismiss = { hideSensitiveData(); onBack() }
    BackHandler(onBack = dismiss)
    androidx.compose.runtime.DisposableEffect(lifecycleOwner, card.cardId) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_STOP) hideSensitiveData()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer); hideSensitiveData() }
    }
    if (reveal != null) androidx.compose.ui.platform.LocalView.current.let { view -> androidx.compose.runtime.DisposableEffect(Unit) { val window = (view.context as? android.app.Activity)?.window; window?.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE); onDispose { window?.clearFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE) } } }
    Scaffold(
        topBar = {
            TopAppBar(title = { Text("卡片详情", fontWeight = FontWeight.SemiBold) }, navigationIcon = {
                IconButton(onClick = dismiss) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "返回") }
            })
        },
    ) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).imePadding().verticalScroll(rememberScrollState()).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            BankCard(shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) { CardFace(card) }
            Text(
                "${card.type} · 尾号 ${card.last4.ifBlank { card.numberMasked.takeLast(4) }}",
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.testTag(UiTags.CARD_DETAIL),
            )
            BankCard(
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                modifier = Modifier.fillMaxWidth().animateContentSize(),
            ) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Icon(Icons.Default.Lock, null, tint = MaterialTheme.colorScheme.secondary, modifier = Modifier.size(20.dp))
                        Text("安全查看卡号", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    }
                    Text("验证后展示完整信息，30 秒后自动隐藏。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (reveal == null) {
                        OutlinedTextField(
                            colors = bankTextFieldColors(),
                            value = pin, onValueChange = { pin = it; error = null },
                            label = { Text("演示卡取款密码") },
                            singleLine = true,
                            enabled = !verifying,
                            isError = error != null,
                            shape = RoundedCornerShape(16.dp),
                            visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
                            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                            modifier = Modifier.fillMaxWidth().testTag(UiTags.CARD_PIN),
                        )
                        error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
                        BankButton(
                            onClick = {
                                if (!lifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) return@BankButton
                                val submittedPin = pin
                                val generation = ++revealGeneration
                                pin = ""
                                error = null
                                verifying = true
                                revealJob = scope.launch {
                                    try {
                                        val response = revealCard(card.cardId, submittedPin)
                                        if (generation == revealGeneration && lifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) {
                                            reveal = response
                                        }
                                    } catch (cancelled: kotlinx.coroutines.CancellationException) {
                                        throw cancelled
                                    } catch (failure: Exception) {
                                        if (generation == revealGeneration && lifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) {
                                            error = "验证失败，请检查取款密码"
                                        }
                                    } finally {
                                        if (generation == revealGeneration) {
                                            verifying = false
                                            revealJob = null
                                        }
                                    }
                                }
                            },
                            enabled = pin.isNotEmpty() && !verifying,
                            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp).testTag(UiTags.CARD_REVEAL),
                        ) { Text(if (verifying) "验证中…" else "查看卡号") }
                    } else {
                        val shown = reveal!!
                        Column(
                            Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.primaryContainer, RoundedCornerShape(16.dp)).padding(16.dp).testTag(UiTags.CARD_REVEALED),
                            verticalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            Text("户名：${shown.ownerName}", style = MaterialTheme.typography.bodyMedium)
                            Text("卡号：${shown.cardNumber}", style = MaterialTheme.typography.titleMedium, fontFamily = FontFamily.Monospace)
                            Text("开户行：${shown.issuerName}", style = MaterialTheme.typography.bodyMedium)
                            Text("本次展示将在 30 秒后失效", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            BankTextButton(onClick = { reveal = null }) { Text("立即隐藏") }
                        }
                    }
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                BankOutlinedButton(
                    onClick = { hideSensitiveData(); onTransfer() },
                    modifier = Modifier.weight(1f).heightIn(min = 52.dp).testTag(UiTags.CARD_TRANSFER),
                ) { Text("转账") }
                BankButton(
                    onClick = { hideSensitiveData(); onBack(); onWealth() },
                    modifier = Modifier.weight(1f).heightIn(min = 52.dp).testTag(UiTags.CARD_WEALTH),
                ) { Text("理财") }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}
