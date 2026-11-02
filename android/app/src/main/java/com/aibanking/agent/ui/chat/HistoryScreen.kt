package com.aibanking.agent.ui.chat

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.aibanking.agent.data.HistoryRecordView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.CascadeEntranceSpec
import com.aibanking.agent.ui.anim.ShimmerSwitch
import com.aibanking.agent.ui.anim.cascadeItem
import com.aibanking.agent.ui.anim.rememberCascadeEntrance
import com.aibanking.agent.ui.components.BankBrandMark
import com.aibanking.agent.ui.components.BankIcon
import com.aibanking.agent.ui.components.BankIconKind
import com.valentinilk.shimmer.shimmer

/**
 * 业务办理历史与交易明细全屏界面。
 * 遵循 Material Design 3 风格，支持流光骨架屏与级联交错动效。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HistoryUi(state: ChatUiState, viewModel: ChatViewModel) {
    Scaffold(
        modifier = Modifier
            .fillMaxSize()
            .testTag(UiTags.HISTORY_SCREEN),
        topBar = {
            TopAppBar(
                navigationIcon = {
                    IconButton(onClick = viewModel::dismissPanels) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                title = {
                    Column {
                        Text("办理历史", style = MaterialTheme.typography.titleLarge)
                        Text(
                            "共 ${state.historyRecords.size} 笔业务流水",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                actions = {
                    IconButton(
                        onClick = viewModel::refreshHistory,
                        enabled = !state.panelBusy,
                    ) {
                        BankIcon(BankIconKind.Refresh, contentDescription = "刷新")
                    }
                },
            )
        },
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
        ) {
            // 时间范围过滤 Chip
            DateScopeFilterRow(
                currentScope = state.historyDateScope,
                onSelectScope = viewModel::setHistoryDateScope,
            )

            // 业务分类过滤 Chip
            CategoryFilterRow(
                currentCategory = state.historyCategory,
                onSelectCategory = viewModel::setHistoryCategory,
            )

            // 收支统计卡片
            HistorySummaryCard(records = state.historyRecords)

            // 列表与骨架屏
            Box(modifier = Modifier.fillMaxSize()) {
                val cascade = rememberCascadeEntrance(
                    ready = !state.panelBusy && state.historyRecords.isNotEmpty(),
                    state.historyRecords,
                    state.historyDateScope,
                    state.historyCategory,
                )

                ShimmerSwitch(
                    loading = state.panelBusy && state.historyRecords.isEmpty(),
                    skeleton = { HistorySkeletonList() },
                ) {
                    when {
                        state.panelError != null && state.historyRecords.isEmpty() -> {
                            PanelLoadFailure(
                                message = state.panelError,
                                onRetry = viewModel::refreshHistory,
                            )
                        }
                        state.historyRecords.isEmpty() -> {
                            HistoryEmptyView()
                        }
                        else -> {
                            LazyColumn(
                                modifier = Modifier
                                    .fillMaxSize()
                                    .testTag(UiTags.HISTORY_LIST),
                                contentPadding = PaddingValues(horizontal = 16.dp, vertical = 10.dp),
                                verticalArrangement = Arrangement.spacedBy(10.dp),
                            ) {
                                itemsIndexed(state.historyRecords, key = { _, r -> r.txId }) { index, record ->
                                    HistoryRecordItem(
                                        record = record,
                                        modifier = Modifier.cascadeItem(
                                            host = cascade,
                                            index = index,
                                            spec = CascadeEntranceSpec.ContactCascade,
                                        ),
                                        onClick = { viewModel.selectHistoryRecord(record) },
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // 电子回单详情底部抽屉
    state.selectedHistoryRecord?.let { record ->
        ElectronicReceiptSheet(
            record = record,
            onDismiss = { viewModel.selectHistoryRecord(null) },
            onAskAgent = { viewModel.queryHistoryInChat(record) },
        )
    }
}

@Composable
private fun DateScopeFilterRow(currentScope: String, onSelectScope: (String) -> Unit) {
    val scopes = listOf(
        "ALL" to "全部时间",
        "TODAY" to "今日",
        "MONTH" to "本月",
        "LAST_MONTH" to "上月",
        "YEAR" to "全年",
    )
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        scopes.forEach { (key, label) ->
            FilterChip(
                selected = currentScope == key,
                onClick = { onSelectScope(key) },
                label = { Text(label, style = MaterialTheme.typography.labelMedium) },
                colors = FilterChipDefaults.filterChipColors(
                    selectedContainerColor = MaterialTheme.colorScheme.primaryContainer,
                    selectedLabelColor = MaterialTheme.colorScheme.onPrimaryContainer,
                ),
            )
        }
    }
}

@Composable
private fun CategoryFilterRow(currentCategory: String?, onSelectCategory: (String?) -> Unit) {
    val categories = listOf(
        null to "全部类型",
        "转账支出" to "转账支出",
        "转账收入" to "转账收入",
        "理财申购" to "理财申购",
        "订阅代扣" to "订阅代扣",
        "餐饮" to "餐饮消费",
    )
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 2.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        categories.forEach { (cat, label) ->
            FilterChip(
                selected = currentCategory == cat,
                onClick = { onSelectCategory(cat) },
                label = { Text(label, style = MaterialTheme.typography.labelMedium) },
                colors = FilterChipDefaults.filterChipColors(
                    selectedContainerColor = MaterialTheme.colorScheme.secondaryContainer,
                    selectedLabelColor = MaterialTheme.colorScheme.onSecondaryContainer,
                ),
            )
        }
    }
}

@Composable
private fun HistorySummaryCard(records: List<HistoryRecordView>) {
    val outflow = records.filter { it.category != "转账收入" }.sumOf { it.amount }
    val inflow = records.filter { it.category == "转账收入" }.sumOf { it.amount }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 14.dp),
            horizontalArrangement = Arrangement.SpaceAround,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("支出合计", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(4.dp))
                Text(
                    "¥ ${String.format(java.util.Locale.CHINA, "%,.2f", outflow)}",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurface,
                )
            }
            Box(
                Modifier
                    .width(1.dp)
                    .height(32.dp)
                    .background(MaterialTheme.colorScheme.outlineVariant)
            )
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("收入合计", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(4.dp))
                Text(
                    "+¥ ${String.format(java.util.Locale.CHINA, "%,.2f", inflow)}",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    color = Color(0xFF2E7D32),
                )
            }
            Box(
                Modifier
                    .width(1.dp)
                    .height(32.dp)
                    .background(MaterialTheme.colorScheme.outlineVariant)
            )
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("总笔数", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(4.dp))
                Text(
                    "${records.size} 笔",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
        }
    }
}

@Composable
private fun HistoryRecordItem(
    record: HistoryRecordView,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val isInflow = record.category == "转账收入"
    val iconKind = when (record.category) {
        "转账支出", "转账收入" -> BankIconKind.Transfer
        "理财申购" -> BankIconKind.Chart
        "订阅代扣" -> BankIconKind.Task
        else -> BankIconKind.Card
    }
    val iconContainerColor = when (record.category) {
        "转账收入" -> Color(0xFFE8F5E9)
        "转账支出" -> Color(0xFFFFEBEE)
        "理财申购" -> MaterialTheme.colorScheme.primaryContainer
        "订阅代扣" -> MaterialTheme.colorScheme.secondaryContainer
        else -> MaterialTheme.colorScheme.surfaceContainerHighest
    }
    val iconTintColor = when (record.category) {
        "转账收入" -> Color(0xFF2E7D32)
        "转账支出" -> Color(0xFFC62828)
        "理财申购" -> MaterialTheme.colorScheme.onPrimaryContainer
        "订阅代扣" -> MaterialTheme.colorScheme.onSecondaryContainer
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }

    Card(
        modifier = modifier
            .fillMaxWidth()
            .testTag(UiTags.historyItem(record.txId))
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f)),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(42.dp)
                    .clip(CircleShape)
                    .background(iconContainerColor),
                contentAlignment = Alignment.Center,
            ) {
                BankIcon(iconKind, modifier = Modifier.size(20.dp), tint = iconTintColor)
            }

            Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text(
                    text = record.merchant,
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Surface(
                        shape = RoundedCornerShape(4.dp),
                        color = MaterialTheme.colorScheme.surfaceContainerHighest,
                    ) {
                        Text(
                            text = record.category,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(horizontal = 4.dp, vertical = 2.dp),
                        )
                    }
                    Text(
                        text = record.time,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.outline,
                    )
                }
                if (!record.remark.isNullOrBlank()) {
                    Text(
                        text = "附言：${record.remark}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }

            Column(horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text(
                    text = if (isInflow) "+¥ ${String.format(java.util.Locale.CHINA, "%,.2f", record.amount)}"
                    else "-¥ ${String.format(java.util.Locale.CHINA, "%,.2f", record.amount)}",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    color = if (isInflow) Color(0xFF2E7D32) else MaterialTheme.colorScheme.onSurface,
                )
                Surface(
                    shape = RoundedCornerShape(10.dp),
                    color = Color(0xFFE8F5E9),
                ) {
                    Text(
                        text = "已完成",
                        style = MaterialTheme.typography.labelSmall,
                        color = Color(0xFF2E7D32),
                        fontWeight = FontWeight.Medium,
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun HistoryEmptyView() {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Box(
            modifier = Modifier
                .size(72.dp)
                .clip(CircleShape)
                .background(MaterialTheme.colorScheme.surfaceContainerHigh),
            contentAlignment = Alignment.Center,
        ) {
            BankIcon(BankIconKind.History, modifier = Modifier.size(36.dp), tint = MaterialTheme.colorScheme.outline)
        }
        Spacer(Modifier.height(16.dp))
        Text("暂无办理记录", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(6.dp))
        Text(
            "您通过对话办理的转账、理财或日常交易均会记录在此。",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun HistorySkeletonList() {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        repeat(5) {
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(82.dp)
                    .shimmer(),
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .size(42.dp)
                            .clip(CircleShape)
                            .background(MaterialTheme.colorScheme.surfaceContainerHighest),
                    )
                    Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth(0.5f)
                                .height(16.dp)
                                .clip(RoundedCornerShape(4.dp))
                                .background(MaterialTheme.colorScheme.surfaceContainerHighest),
                        )
                        Box(
                            modifier = Modifier
                                .fillMaxWidth(0.35f)
                                .height(12.dp)
                                .clip(RoundedCornerShape(4.dp))
                                .background(MaterialTheme.colorScheme.surfaceContainerHighest),
                        )
                    }
                    Box(
                        modifier = Modifier
                            .width(60.dp)
                            .height(20.dp)
                            .clip(RoundedCornerShape(4.dp))
                            .background(MaterialTheme.colorScheme.surfaceContainerHighest),
                    )
                }
            }
        }
    }
}

/** 电子回单 / 业务凭证弹窗 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ElectronicReceiptSheet(
    record: HistoryRecordView,
    onDismiss: () -> Unit,
    onAskAgent: () -> Unit,
) {
    val clipboard = LocalClipboardManager.current
    var copied by remember { mutableStateOf(false) }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
        shape = RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp),
        modifier = Modifier.testTag(UiTags.HISTORY_DETAIL_DIALOG),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp)
                .padding(bottom = 24.dp)
                .navigationBarsPadding(),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            // 回单头部
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    BankBrandMark(Modifier.size(24.dp))
                    Text(
                        "业务办理凭证",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                }
                Surface(
                    shape = RoundedCornerShape(12.dp),
                    color = Color(0xFFE8F5E9),
                ) {
                    Text(
                        "● 记账成功",
                        style = MaterialTheme.typography.labelSmall,
                        color = Color(0xFF2E7D32),
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                    )
                }
            }

            // 金额展示
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(18.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(
                        record.category,
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "¥ ${String.format(java.util.Locale.CHINA, "%,.2f", record.amount)}",
                        style = MaterialTheme.typography.headlineMedium,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }

            // 明细列表
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                ReceiptDetailRow(label = "对方户名 / 商户", value = record.merchant)
                if (!record.counterpartyId.isNullOrBlank()) {
                    ReceiptDetailRow(label = "对方账户", value = record.counterpartyId)
                }
                if (!record.cardId.isNullOrBlank()) {
                    ReceiptDetailRow(label = "关联卡片", value = record.cardId)
                }
                ReceiptDetailRow(label = "交易时间", value = record.time)
                ReceiptDetailRow(
                    label = "交易流水号",
                    value = record.txId,
                    isMono = true,
                    action = {
                        TextButton(
                            onClick = {
                                clipboard.setText(AnnotatedString(record.txId))
                                copied = true
                            },
                            contentPadding = PaddingValues(horizontal = 6.dp, vertical = 2.dp),
                        ) {
                            Text(
                                if (copied) "已复制" else "复制",
                                style = MaterialTheme.typography.labelSmall,
                            )
                        }
                    },
                )
                if (!record.remark.isNullOrBlank()) {
                    ReceiptDetailRow(label = "附言 / 备注", value = record.remark)
                }
                ReceiptDetailRow(label = "清算通道", value = "实时清算系统 (Mock Core)")
            }

            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

            // 操作按钮
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Button(
                    onClick = onAskAgent,
                    modifier = Modifier
                        .weight(1f)
                        .testTag(UiTags.HISTORY_ASK_AGENT),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    BankIcon(BankIconKind.Chat, modifier = Modifier.size(18.dp), tint = Color.White)
                    Spacer(Modifier.width(6.dp))
                    Text("向 Agent 追问此笔流水")
                }
                OutlinedButton(
                    onClick = onDismiss,
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text("关闭")
                }
            }
        }
    }
}

@Composable
private fun ReceiptDetailRow(
    label: String,
    value: String,
    isMono: Boolean = false,
    action: (@Composable () -> Unit)? = null,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(
                value,
                style = if (isMono) MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace)
                else MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.onSurface,
            )
            action?.invoke()
        }
    }
}
