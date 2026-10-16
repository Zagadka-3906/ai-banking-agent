package com.aibanking.agent.ui.chat

import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.PredictiveBackPreview
import com.aibanking.agent.ui.anim.ShimmerSwitch
import com.aibanking.agent.ui.anim.SkeletonBlock
import com.aibanking.agent.ui.anim.cascadeItem
import com.aibanking.agent.ui.anim.pageSlideSpec
import com.aibanking.agent.ui.anim.rememberCascadeEntrance
import com.aibanking.agent.ui.components.BankCard
import com.aibanking.agent.ui.components.BankSemanticStatus
import com.aibanking.agent.ui.components.BankStatusChip
import com.aibanking.agent.ui.components.BankSticker
import com.aibanking.agent.ui.components.BankTextButton
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import java.util.Locale

// Wealth screens consume the shared semantic palette so the Chinese finance
// convention (red=gain, green=loss) and dark-mode contrast stay consistent.
internal val WealthPositive: Color @Composable get() = LocalBankSemanticColors.current.positive
internal val WealthNegative: Color @Composable get() = LocalBankSemanticColors.current.negative
internal val WealthRiskYellow: Color @Composable get() = LocalBankSemanticColors.current.riskYellow
internal val WealthChartBenchmark: Color @Composable get() = LocalBankSemanticColors.current.chartBenchmark

/**
 * 银行风格理财页：产品超市 → 产品详情 → 申购。
 *
 * 布局仿手机银行“理财”频道：顶部风险档案横幅、我的持仓、在售产品卡片
 * （类别角标 + 风险等级徽标 + 业绩比较基准大字 + 起购/期限）。申购不在
 * 页面里做资金操作——填金额后预填聊天话术，回到对话走红色风险审批
 * （短信 + 设备签名），与服务端“所有变更必须过统一授权网关”一致。
 *
 * 业绩比较基准不是收益承诺，页面固定展示风险提示（银行规范口径）。
 */

@Composable
fun WealthUi(state: ChatUiState, viewModel: ChatViewModel, snackbar: SnackbarHostState) {
    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        PredictiveBackPreview(
            enabled = state.wealthScreen != WealthScreen.LIST,
            onBack = viewModel::backToWealthList,
            beneath = { WealthListScreen(state, viewModel, snackbar) },
        ) {
            AnimatedContent(
                targetState = state.wealthScreen,
                transitionSpec = { pageSlideSpec { it.ordinal } },
                label = "wealthScreen",
            ) { screen ->
                when (screen) {
                    WealthScreen.LIST -> WealthListScreen(state, viewModel, snackbar)
                    WealthScreen.DETAIL -> WealthDetailScreen(state, viewModel, snackbar)
                }
            }
        }
    }
}
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun WealthListScreen(state: ChatUiState, viewModel: ChatViewModel, snackbar: SnackbarHostState) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("理财") },
                navigationIcon = {
                    IconButton(onClick = viewModel::closeWealth) { Icon(Icons.Default.Close, contentDescription = "返回聊天") }
                },
                actions = {
                    Text(
                        "${state.wealthProducts.size} 款在售",
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.padding(end = 16.dp),
                    )
                },
            )
        },
    ) { padding ->
        // 流光骨架屏：加载中呈现横幅/持仓/产品卡形占位（45° 高光扫过），数据
        // 就绪 200ms 交叉淡入真实列表。
        ShimmerSwitch(
            loading = state.panelBusy && state.wealthProducts.isEmpty(),
            modifier = Modifier.fillMaxSize(),
            skeleton = { WealthSkeleton(Modifier.fillMaxSize().padding(padding)) },
        ) {
            val loadFailure = state.panelError
            if (loadFailure != null && state.wealthProducts.isEmpty()) {
                // 一次失败的加载会把产品、持仓、风险档案一起留空；此时页面若继续渲染
                // “尚未完成风险测评 / 暂无持仓”，就是把“没问到核心”说成了用户的资产状况。
                Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                    PanelLoadFailure(loadFailure, viewModel::refreshWealth)
                }
                return@ShimmerSwitch
            }
            // 级联入场：数据就绪/刷新完成时，可见区前 8 个子项自下而上波浪进场。
            val cascade = rememberCascadeEntrance(
                ready = state.wealthProducts.isNotEmpty() || state.wealthHoldings.isNotEmpty(),
            )
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding).testTag(UiTags.WEALTH_LIST),
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 12.dp, bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            // 扁平序号跨越横幅、标题与卡片，波浪按整页顺序推入。
            var flat = 0
            item {
                BankCard(
                    modifier = Modifier.fillMaxWidth().cascadeItem(cascade, flat++),
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
                ) {
                    Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        BankSticker("财富管理 · 持仓概览")
                        Text("稳步积累，从容规划", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onPrimaryContainer)
                        Text("持仓本金", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = .72f))
                        Text("¥%,.2f".format(Locale.CHINA, state.wealthHoldings.sumOf { it.principal }), style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onPrimaryContainer)
                        Text("${state.wealthHoldings.size} 笔持仓 · ${state.wealthProducts.size} 款在售产品", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = .72f))
                    }
                }
            }
            item {
                RiskProfileBanner(state, viewModel, Modifier.cascadeItem(cascade, flat++))
            }
            item {
                SectionHeader("我的持仓", Modifier.cascadeItem(cascade, flat++))
            }
            if (state.wealthHoldings.isEmpty()) {
                item {
                    Text(
                        "暂无持仓。选择下方产品申购后，这里会展示持仓与本金。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                items(
                    count = state.wealthHoldings.size,
                    key = { state.wealthHoldings[it].holdingId },
                ) { index ->
                    val holding = state.wealthHoldings[index]
                    BankCard(
                        modifier = Modifier.fillMaxWidth().cascadeItem(cascade, flat++),
                        shape = RoundedCornerShape(16.dp),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                    ) {
                        Column(
                            Modifier.fillMaxWidth().padding(20.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Text(holding.productName, style = MaterialTheme.typography.titleSmall)
                                Text(
                                    holding.holdingId,
                                    style = MaterialTheme.typography.labelSmall,
                                    fontFamily = FontFamily.Monospace,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Text(
                                "¥%,.2f".format(Locale.CHINA, holding.principal),
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                            )
                        }
                    }
                }
            }
            item {
                SectionHeader("在售产品", Modifier.cascadeItem(cascade, flat++))
            }
            items(
                count = state.wealthProducts.size,
                // 刷新时保留产品卡片的稳定身份。
                key = { state.wealthProducts[it].productId },
            ) { index ->
                val product = state.wealthProducts[index]
                ProductCard(product, Modifier.cascadeItem(cascade, flat++)) {
                    viewModel.showProductDetail(product.productId)
                }
            }
            item {
                Text(
                    "业绩比较基准不代表未来收益承诺，理财非存款、产品有风险、投资须谨慎。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.cascadeItem(cascade, flat++).padding(top = 4.dp),
                )
            }
            }
        }
    }
}

/** 加载中的理财骨架：横幅/持仓/标题/产品卡的排版占位（含高光扫过）。 */
@Composable
private fun WealthSkeleton(modifier: Modifier = Modifier) {
    Column(modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
        SkeletonBlock(height = 188.dp, corner = 24.dp)
        SkeletonBlock(height = 104.dp, corner = 24.dp)
        SkeletonBlock(width = 88.dp, height = 16.dp, corner = 4.dp)
        repeat(2) {
            SkeletonBlock(height = 200.dp, corner = 24.dp)
        }
    }
}

@Composable
private fun ProductCard(product: ProductView, modifier: Modifier = Modifier, onClick: () -> Unit) {
    BankCard(
        modifier = modifier
            .fillMaxWidth()
            .testTag(UiTags.wealthProduct(product.productId))
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
    ) {
        Column(Modifier.fillMaxWidth().padding(24.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                CategoryChip(product.category ?: "理财产品")
                RiskBadge(product.riskLevel)
            }
            Text(product.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text(
                product.benchmark ?: "业绩比较基准以产品说明书为准",
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.secondary,
                fontWeight = FontWeight.Bold,
            )
            Text(
                buildString {
                    append("起购 ¥%,.0f".format(Locale.CHINA, product.minAmount))
                    product.term?.let { append(" · $it") }
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun RiskProfileBanner(state: ChatUiState, viewModel: ChatViewModel, modifier: Modifier = Modifier) {
    val profile = state.riskProfile
    BankCard(
        colors = CardDefaults.cardColors(
            containerColor = if (profile == null) MaterialTheme.colorScheme.tertiaryContainer else MaterialTheme.colorScheme.primaryContainer,
        ),
        modifier = modifier.fillMaxWidth(),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(20.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    if (profile == null) "尚未完成风险测评" else "风险档案：${profileLabel(profile.riskProfile)}",
                    style = MaterialTheme.typography.titleSmall,
                )
                Text(
                    if (profile == null) "申购前需完成风险评估问卷" else "测评结果决定可申购的产品风险等级",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            BankTextButton(
                onClick = {
                    viewModel.onInputChange(if (profile == null) "我想做风险测评" else "我想重新做风险测评")
                    viewModel.closeWealth()
                },
            ) { Text(if (profile == null) "去测评" else "重新测评") }
        }
    }
}
// ---------------------------------------------------------------------------
// shared pieces
// ---------------------------------------------------------------------------

@Composable
private fun SectionHeader(title: String, modifier: Modifier = Modifier) {
    Text(
        text = title,
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.Bold,
        modifier = modifier.padding(top = 6.dp),
    )
}

@Composable
internal fun CategoryChip(text: String) {
    Box(
        Modifier
            .background(MaterialTheme.colorScheme.secondaryContainer, RoundedCornerShape(8.dp))
            .padding(horizontal = 10.dp, vertical = 6.dp),
    ) {
        Text(text, style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
internal fun RiskBadge(level: String) {
    val status = when (level) {
        "R1" -> BankSemanticStatus.RiskGreen
        "R2" -> BankSemanticStatus.Neutral
        "R3" -> BankSemanticStatus.RiskYellow
        else -> BankSemanticStatus.RiskRed
    }
    BankStatusChip(level, status)
}

@Composable
internal fun WealthDetailRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(96.dp),
        )
        Text(value, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
    }
}

private fun profileLabel(profile: String): String = when (profile) {
    "CONSERVATIVE", "C1" -> "保守型"
    "C2" -> "稳健型"
    "BALANCED", "C3" -> "平衡型"
    "C4" -> "进取型"
    "AGGRESSIVE", "C5" -> "激进型"
    else -> profile
}
