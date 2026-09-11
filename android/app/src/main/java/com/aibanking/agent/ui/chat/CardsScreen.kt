package com.aibanking.agent.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.material3.CircularProgressIndicator
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
import java.util.Locale

/**
 * 银行风格“我的银行卡”页：仿真卡面（渐变卡板 + 脱敏卡号 + 余额）+ 卡片
 * 操作 + 自行添加银行卡。
 *
 * 余额与所属账户共享（借记卡语义）；冻结/挂失属于红色风险操作，页面把
 * 话术预填进聊天，由核心的短信 + 设备签名审批流最终授权。添加卡片为
 * 绿区操作，核心开立即生效并审计。
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
        if (state.panelBusy && state.cards.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            return@Scaffold
        }
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            items(state.cards.size) { index ->
                BankCardVisual(state.cards[index], viewModel)
            }
            if (state.cards.isEmpty() && !state.panelBusy) {
                item {
                    Text(
                        "还没有银行卡。点右下角即可自行添加。",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            item {
                Text(
                    "冻结、挂失与额度调整为红色风险操作：点击后回到对话确认，需短信验证码与设备签名。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
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
            onAdd = {
                viewModel.addCard()
                showAdd = false
            },
            onDismiss = { showAdd = false },
        )
    }
}

@Composable
private fun BankCardVisual(card: CardView, viewModel: ChatViewModel) {
    val frozen = card.status != "ACTIVE"
    Card(
        modifier = Modifier.fillMaxWidth(),
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
                    card.numberMasked,
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
                if (frozen) {
                    ActionChip("解冻", enabled = true) { viewModel.startCardAction("unfreeze", card) }
                } else {
                    ActionChip("冻结", enabled = true) { viewModel.startCardAction("freeze", card) }
                    ActionChip("挂失", enabled = true) { viewModel.startCardAction("report_lost", card) }
                }
            }
            Text(
                "操作将回到对话确认：冻结/挂失为高风险操作，需短信验证码与设备签名。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
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
private fun ActionChip(text: String, enabled: Boolean, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, enabled = enabled, modifier = Modifier.testTag(UiTags.CARD_ACTION)) {
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
    onAdd: () -> Unit,
    onDismiss: () -> Unit,
) {
    if (!visible) return
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp)
                .padding(bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("添加银行卡", style = MaterialTheme.typography.titleMedium)
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
                onClick = onAdd,
                enabled = !busy,
                modifier = Modifier.fillMaxWidth().testTag(UiTags.CARD_ADD),
            ) { Text(if (busy) "开卡中…" else "确认添加") }
            Text(
                "开立即生效并计入审计；卡号为虚构演示号段，仅显示脱敏形式。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
