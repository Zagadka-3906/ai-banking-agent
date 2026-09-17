package com.aibanking.agent.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.activity.compose.BackHandler
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import android.Manifest
import android.content.pm.PackageManager
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import kotlinx.coroutines.launch
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aibanking.agent.data.CardView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.ShimmerSwitch
import com.aibanking.agent.ui.anim.SkeletonBlock
import com.aibanking.agent.ui.anim.cascadeItem
import com.aibanking.agent.ui.anim.rememberCascadeEntrance
import java.util.Locale

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
    val snackbar = remember { SnackbarHostState() }
    LaunchedEffect(state.error, state.notice) {
        val message = state.error ?: state.notice
        if (message != null) {
            snackbar.showSnackbar(message)
            viewModel.dismissMessages()
        }
    }
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
                title = { Text("我的银行卡") },
                navigationIcon = {
                    IconButton(onClick = viewModel::closeCards) { Icon(Icons.Default.Close, contentDescription = "返回聊天") }
                },
                actions = {
                    Text(
                        "${state.cards.size} 张",
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.padding(end = 16.dp),
                    )
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
        floatingActionButton = {
            FloatingActionButton(
                onClick = { showAdd = true },
                modifier = Modifier.testTag(UiTags.CARD_OPEN_ADD),
            ) { Icon(Icons.Default.Add, contentDescription = "添加银行卡") }
        },
    ) { padding ->
        // 流光骨架屏：加载中呈现卡面形占位（45° 高光扫过），数据就绪 200ms
        // 交叉淡入真实列表。
        ShimmerSwitch(
            loading = state.panelBusy && state.cards.isEmpty(),
            modifier = Modifier.fillMaxSize().padding(padding),
            skeleton = { CardsSkeleton() },
        ) {
            // 级联入场：首次加载/刷新完成时，前 8 个子项自下而上波浪进场。
            val cascade = rememberCascadeEntrance(ready = state.cards.isNotEmpty())
            LazyColumn(
                // Tagged so a test can scroll: the list is lazy, so a card face that is
                // outside the viewport is not composed at all and cannot be found by tag.
                modifier = Modifier.fillMaxSize().testTag(UiTags.CARD_LIST),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
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
                            // 请求失败 ≠ 没有银行卡：报错并可重试，不谎报“还没有银行卡”。
                            PanelLoadFailure(loadFailure, viewModel::refreshCards)
                        } else {
                            Text(
                                "还没有银行卡。点右下角即可自行添加。",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
                item {
                    Text(
                        "冻结、挂失与额度调整为红色风险操作：点击后回到对话确认，需短信验证码与设备签名。",
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
                // panelBusy 期间不受理第二次提交：按钮虽然已禁用，但这里要挡住
                // “按下按钮的同时另一次绑定已在飞”的那一下连点。
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
    selectedCard?.let { card -> CardDetail(card, viewModel) { selectedCard = null } }
}

/** 加载中的卡面骨架：与真实卡片排版一致的占位块（含高光扫过）。 */
@Composable
private fun CardsSkeleton() {
    Column(
        Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        repeat(3) {
            SkeletonBlock(height = 190.dp, corner = 16.dp)
        }
    }
}

@Composable
private fun BankCardVisual(card: CardView, viewModel: ChatViewModel, modifier: Modifier = Modifier, onOpen: () -> Unit = {}) {
    val frozen = card.status != "ACTIVE"
    Card(
        modifier = modifier.fillMaxWidth().clickable(onClick = onOpen).testTag("${UiTags.CARD_DETAIL}:${card.cardId}"),
        shape = RoundedCornerShape(16.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .background(
                    Brush.linearGradient(
                        if (frozen) listOf(Color(0xFF546E7A), Color(0xFF37474F))
                        else listOf(Color(0xFF1A3C6E), Color(0xFF2E6DA4)),
                    ),
                )
                .padding(16.dp),
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Text("AI 银行", color = Color.White, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    StatusBadge(card.status)
                }
                Spacer(Modifier.height(6.dp))
                Text(
                    "可用余额",
                    color = Color.White.copy(alpha = 0.75f),
                    style = MaterialTheme.typography.labelSmall,
                )
                Text(
                    "¥" + String.format(Locale.CHINA, "%,.2f", card.availableBalance),
                    color = Color.White,
                    style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    "尾号 ${card.last4.ifBlank { card.numberMasked.takeLast(4) }}",
                    color = Color.White,
                    style = MaterialTheme.typography.titleMedium,
                    fontFamily = FontFamily.Monospace,
                    letterSpacing = 2.sp,
                )
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Column {
                        Text("${card.type}${card.label?.let { " · $it" } ?: ""}", color = Color.White.copy(alpha = 0.85f), style = MaterialTheme.typography.labelSmall)
                        Text("日限额 ¥%,.0f".format(Locale.CHINA, card.dailyLimit), color = Color.White.copy(alpha = 0.85f), style = MaterialTheme.typography.labelSmall)
                    }
                    Column(horizontalAlignment = Alignment.End) {
                        Text("今日已用 ¥%,.2f".format(Locale.CHINA, card.dailySpentToday), color = Color.White.copy(alpha = 0.85f), style = MaterialTheme.typography.labelSmall)
                        card.dailyRemaining?.let {
                            Text("今日剩余 ¥%,.2f".format(Locale.CHINA, it), color = Color.White.copy(alpha = 0.85f), style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
            }
        }
        Column(Modifier.fillMaxWidth().padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                cardActions(card.status).forEach { action ->
                    ActionChip(actionLabel(action), card, action) { viewModel.startCardAction(action, card) }
                }
            }
            Text(
                "操作将回到对话确认：冻结/挂失/解挂为高风险操作，需短信验证码与设备签名。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
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
    val (label, color) = when (status) {
        "ACTIVE" -> "正常" to Color(0xFF81C784)
        "FROZEN" -> "已冻结" to Color(0xFFFFB74D)
        "LOST" -> "已挂失" to Color(0xFFE57373)
        else -> status to Color(0xFFB0BEC5)
    }
    Box(
        Modifier
            .background(color, RoundedCornerShape(6.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp),
    ) {
        Text(label, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun ActionChip(text: String, card: CardView, action: String, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, modifier = Modifier.testTag(UiTags.cardAction(card.cardId, action))) {
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
    ModalBottomSheet(onDismissRequest = onDismiss) {
        var number by remember { mutableStateOf("") }
        var source by remember { mutableStateOf("MANUAL") }
        var confirmed by remember { mutableStateOf(false) }
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
                .padding(horizontal = 16.dp)
                .padding(bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("添加银行卡", style = MaterialTheme.typography.titleMedium)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(value = number, onValueChange = { number = it; source = "MANUAL"; confirmed = false; error = null }, label = { Text("卡号（必填）") }, keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Number), singleLine = true, modifier = Modifier.weight(1f).testTag(UiTags.CARD_NUMBER))
                OutlinedButton(onClick = { if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) camera.launch(null) else permission.launch(Manifest.permission.CAMERA) }, modifier = Modifier.testTag(UiTags.CARD_CAMERA)) { Text("拍照") }
            }
            androidx.compose.material3.Checkbox(checked = confirmed, onCheckedChange = { confirmed = it }, modifier = Modifier.testTag(UiTags.CARD_CONFIRM))
            Text("我确认卡号属于本人且输入无误", style = MaterialTheme.typography.bodySmall)
            error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            Text("卡片类型", style = MaterialTheme.typography.labelLarge)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("借记卡", "虚拟卡").forEach { option ->
                    FilterChip(
                        selected = cardType == option,
                        onClick = { onFieldChange(option, null) },
                        label = { Text(option) },
                    )
                }
            }
            OutlinedTextField(
                value = label,
                onValueChange = { onFieldChange(null, it) },
                label = { Text("备注（可选，例如：工资卡）") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Button(
                onClick = { onAdd(number, source) },
                enabled = !busy && confirmed && extractCardNumber(number) != null,
                modifier = Modifier.fillMaxWidth().testTag(UiTags.CARD_ADD),
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
private fun CardDetail(card: CardView, viewModel: ChatViewModel, onBack: () -> Unit) {
    var pin by remember(card.cardId) { mutableStateOf("") }
    var reveal by remember(card.cardId) { mutableStateOf<com.aibanking.agent.data.CardRevealView?>(null) }
    var error by remember(card.cardId) { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
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
    BackHandler(onBack = onBack)
    androidx.compose.runtime.DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_STOP) { reveal = null; pin = "" } }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer); reveal = null; pin = "" }
    }
    if (reveal != null) androidx.compose.ui.platform.LocalView.current.let { view -> androidx.compose.runtime.DisposableEffect(Unit) { val window = (view.context as? android.app.Activity)?.window; window?.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE); onDispose { window?.clearFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE) } } }
    Scaffold(topBar = { TopAppBar(title = { Text("卡片详情") }, navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "返回") } }) }) { padding ->
        Column(Modifier.padding(padding).padding(16.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text("${card.type} · 尾号 ${card.last4.ifBlank { card.numberMasked.takeLast(4) }}", style = MaterialTheme.typography.titleLarge, modifier = Modifier.testTag(UiTags.CARD_DETAIL))
            Text("状态：${card.status}")
            if (reveal == null) {
                OutlinedTextField(pin, { pin = it }, label = { Text("演示卡取款密码") }, visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(), keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.NumberPassword), modifier = Modifier.fillMaxWidth().testTag(UiTags.CARD_PIN))
                Button(onClick = { val p = pin; pin = ""; error = null; scope.launch { try { reveal = viewModel.revealCard(card.cardId, p); error = null } catch (e: Exception) { error = "验证失败，请检查取款密码" } } }, enabled = pin.isNotEmpty(), modifier = Modifier.testTag(UiTags.CARD_REVEAL)) { Text("查看卡号") }
            } else {
                val shown = reveal!!
                Column(Modifier.testTag(UiTags.CARD_REVEALED), verticalArrangement = Arrangement.spacedBy(8.dp)) { Text("户名：${shown.ownerName}"); Text("卡号：${shown.cardNumber}", fontFamily = FontFamily.Monospace); Text("开户行：${shown.issuerName}"); Text("本次展示将在 30 秒后失效") }
            }
            error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) { OutlinedButton(onClick = { reveal = null; viewModel.prefillCardTransfer(card) }, modifier = Modifier.testTag(UiTags.CARD_TRANSFER)) { Text("转账") }; OutlinedButton(onClick = { reveal = null; onBack(); viewModel.openWealth() }, modifier = Modifier.testTag(UiTags.CARD_WEALTH)) { Text("理财") } }
        }
    }
}
