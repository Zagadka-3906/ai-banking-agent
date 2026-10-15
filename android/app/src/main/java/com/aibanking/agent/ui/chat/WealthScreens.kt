package com.aibanking.agent.ui.chat

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.PredictiveBackPreview
import com.aibanking.agent.ui.anim.ShimmerSwitch
import com.aibanking.agent.ui.anim.SkeletonBlock
import com.aibanking.agent.ui.anim.cascadeItem
import com.aibanking.agent.ui.anim.pageSlideSpec
import com.aibanking.agent.ui.anim.rememberCascadeEntrance
import com.aibanking.agent.ui.components.BankButton
import com.aibanking.agent.ui.components.BankCard
import com.aibanking.agent.ui.components.BankFilterChip
import com.aibanking.agent.ui.components.BankOutlinedButton
import com.aibanking.agent.ui.components.BankSectionCard
import com.aibanking.agent.ui.components.BankSectionHeader
import com.aibanking.agent.ui.components.BankSemanticStatus
import com.aibanking.agent.ui.components.BankStatusChip
import com.aibanking.agent.ui.components.BankSticker
import com.aibanking.agent.ui.components.BankTextButton
import com.aibanking.agent.ui.components.bankTextFieldColors
import com.aibanking.agent.ui.theme.BankSpacing
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import java.time.LocalDate
import java.util.Locale
import kotlinx.coroutines.launch

// Wealth screens consume the shared semantic palette so the Chinese finance
// convention (red=gain, green=loss) and dark-mode contrast stay consistent.
private val WealthPositive: Color @Composable get() = LocalBankSemanticColors.current.positive
private val WealthNegative: Color @Composable get() = LocalBankSemanticColors.current.negative
private val WealthRiskYellow: Color @Composable get() = LocalBankSemanticColors.current.riskYellow
private val WealthChartBenchmark: Color @Composable get() = LocalBankSemanticColors.current.chartBenchmark

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
// detail
// ---------------------------------------------------------------------------

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun WealthDetailScreen(state: ChatUiState, viewModel: ChatViewModel, snackbar: SnackbarHostState) {
    val product = state.selectedProduct
    var lastRendered by remember { mutableStateOf<ProductView?>(null) }
    SideEffect { if (product != null) lastRendered = product }
    val rendered = if (state.wealthScreen == WealthScreen.DETAIL) product else lastRendered
    var selectedRange by remember { mutableStateOf("近1年") }
    var selectedTab by remember { mutableStateOf("单位净值") }
    var historyExpanded by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    fun notice(message: String) { scope.launch { snackbar.showSnackbar(message) } }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column(Modifier.testTag(UiTags.WEALTH_HEADER)) {
                        Text(
                            rendered?.name ?: "产品详情",
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                        )
                        Text(
                            rendered?.productId ?: "",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            fontFamily = FontFamily.Monospace,
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = viewModel::backToWealthList) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回列表")
                    }
                },
            )
        },
        bottomBar = {
            if (rendered != null) {
                PurchaseBar(state, viewModel, rendered)
            }
        },
    ) { padding ->
        if (rendered == null) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                Text("产品不存在或已下架。", style = MaterialTheme.typography.bodyMedium)
            }
            return@Scaffold
        }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .background(MaterialTheme.colorScheme.background)
                .verticalScroll(rememberScrollState())
                .testTag(UiTags.WEALTH_DETAIL)
                .padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            WealthSummary(rendered) { notice(rendered.promoSubtitle ?: rendered.promoTitle ?: "暂无更多优惠信息") }

            BankCard(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            ) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    Text("业绩走势", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Row(
                        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        listOf("单位净值", "累计净值", "同类排名").forEach { tab ->
                            BankFilterChip(
                                selected = tab == selectedTab,
                                onClick = { selectedTab = tab },
                                label = { Text(tab) },
                            )
                        }
                    }
                    if (selectedTab != "单位净值") {
                        Text("暂无独立的${selectedTab}时序，当前展示单位净值。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    FlowRow(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(16.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Box(Modifier.size(8.dp).background(LocalBankSemanticColors.current.chartPrimary, CircleShape))
                            Text("本产品", style = MaterialTheme.typography.labelMedium, color = LocalBankSemanticColors.current.chartPrimary)
                        }
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Box(Modifier.size(8.dp).background(WealthChartBenchmark, CircleShape))
                            Text("业绩比较基准", style = MaterialTheme.typography.labelMedium, color = WealthChartBenchmark)
                        }
                    }
                    WealthChart(rendered, selectedRange, Modifier.fillMaxWidth().testTag(UiTags.WEALTH_CHART))
                    Row(
                        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        listOf("近1月", "近3月", "近6月", "近1年", "成立以来").forEach { range ->
                            BankFilterChip(
                                selected = range == selectedRange,
                                onClick = { selectedRange = range },
                                modifier = Modifier.heightIn(min = 48.dp).testTag(UiTags.wealthRange(range)),
                                label = {
                                    Text(if (range == selectedRange) "● $range" else range, style = MaterialTheme.typography.labelMedium)
                                },
                            )
                        }
                    }
                    Text(
                        "归一化走势：产品净值 / 业绩比较基准 · Mock 演示数据，非实时",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // 3. 历史业绩 / 阶段涨幅表格
            BankCard(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_HISTORY).animateContentSize(),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            ) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Text("阶段涨幅", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, color = WealthPositive)
                            Text("历史净值", style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }

                    Row(
                        Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(4.dp)).padding(vertical = 6.dp, horizontal = 4.dp),
                    ) {
                        listOf("阶段区间", "涨跌幅", "同类平均", "同类排名").forEachIndexed { i, col ->
                            Text(
                                col,
                                Modifier.weight(if (i == 0 || i == 3) 1.1f else 1.0f),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                textAlign = if (i == 0) TextAlign.Start else TextAlign.Center,
                            )
                        }
                    }

                    if (rendered.performanceHistory.isEmpty()) {
                        Text("暂无历史业绩数据", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    } else {
                        rendered.performanceHistory.take(if (historyExpanded) Int.MAX_VALUE else 3).forEach { row ->
                            Row(
                                Modifier.fillMaxWidth().padding(vertical = 6.dp, horizontal = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(row.period.ifBlank { "—" }, Modifier.weight(1.1f), style = MaterialTheme.typography.bodySmall)
                                Text(
                                    row.productReturnPct?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "—",
                                    Modifier.weight(1.0f),
                                    style = MaterialTheme.typography.bodySmall,
                                    fontWeight = FontWeight.Bold,
                                    color = returnColor(row.productReturnPct),
                                    textAlign = TextAlign.Center,
                                )
                                val peer = row.peerAveragePct
                                Text(
                                    peer?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "—",
                                    Modifier.weight(1.0f),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = returnColor(peer),
                                    textAlign = TextAlign.Center,
                                )
                                Text(
                                    row.rank ?: "—",
                                    Modifier.weight(1.1f),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    textAlign = TextAlign.Center,
                                )
                            }
                        }
                    }

                    BankTextButton(
                        onClick = { historyExpanded = !historyExpanded },
                        modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_HISTORY_TOGGLE),
                    ) {
                        Text(if (historyExpanded) "收起 ⌃" else "展开更多 ⌄", color = MaterialTheme.colorScheme.secondary, style = MaterialTheme.typography.labelMedium)
                    }
                }
            }

            // 4. 风格特征 (2x2 四宫格卡片)
            BankCard(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_PROFILE),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            ) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Text("风格特征", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                                Text("近1年投资风格与特征画像", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        Text("›", color = MaterialTheme.colorScheme.outline)
                    }

                    if (rendered.analysisMetrics.isEmpty()) {
                        Text("暂无分析指标")
                    } else {
                        rendered.analysisMetrics.chunked(2).forEachIndexed { rowIdx, pair ->
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                pair.forEachIndexed { colIdx, metric ->
                                    val barColor = when (rowIdx * 2 + colIdx) {
                                        0 -> MaterialTheme.colorScheme.primary
                                        1 -> WealthRiskYellow
                                        2 -> WealthNegative
                                        else -> WealthPositive
                                    }
                                    BankCard(
                                        modifier = Modifier.weight(1f),
                                        shape = RoundedCornerShape(8.dp),
                                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                                    ) {
                                        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                            Text(metric.label.ifBlank { "未命名指标" }, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Bold)
                                            Text(metric.value ?: metric.score?.let { "$it/5" } ?: "未提供", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface)
                                            Text(metric.description ?: "暂无说明", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                            metric.score?.takeIf { it in 0..5 }?.let { score ->
                                                Box(Modifier.fillMaxWidth().height(3.dp).background(barColor.copy(alpha = 0.25f), RoundedCornerShape(2.dp))) {
                                                    Box(Modifier.fillMaxWidth(score / 5f).height(3.dp).background(barColor, RoundedCornerShape(2.dp)))
                                                }
                                            }
                                        }
                                    }
                                }
                                if (pair.size == 1) Spacer(Modifier.weight(1f))
                            }
                        }
                    }
                }
            }

            // 5. 基金经理卡片
            BankCard(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_MANAGER),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            ) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("基金经理", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Text("历任基金经理 ›", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.secondary)
                    }

                    Row(
                        Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Box(
                            Modifier
                                .width(50.dp)
                                .height(50.dp)
                                .clip(CircleShape)
                                .background(MaterialTheme.colorScheme.primaryContainer),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(
                                rendered.manager?.name?.take(1) ?: "经",
                                fontWeight = FontWeight.Bold,
                                fontSize = 20.sp,
                                color = MaterialTheme.colorScheme.secondary,
                            )
                        }

                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text(rendered.manager?.name ?: "暂无经理信息", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleSmall)
                            Text(
                                wealthManagerFacts(rendered),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Text(
                                rendered.manager?.bio ?: "经理简介未提供",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }

                        Column(horizontalAlignment = Alignment.End) {
                            val tenureRet = rendered.manager?.tenureReturnPct
                            Text(
                                tenureRet?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "—",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.ExtraBold,
                                color = returnColor(tenureRet),
                            )
                            Text("任职回报", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }

                    Text(
                        wealthInstitutionFacts(rendered),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // 6. 资产配置与持仓分布卡片
            BankCard(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_ALLOCATION),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            ) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("持仓分布", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Text("重仓详情 ›", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.secondary)
                    }

                    if (rendered.assetAllocation.isEmpty()) {
                        Text("暂无资产配置数据")
                    } else {
                        // 比例图例
                        FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            val allocColors = listOf(MaterialTheme.colorScheme.primary, WealthRiskYellow, WealthNegative, MaterialTheme.colorScheme.secondary)
                            rendered.assetAllocation.forEachIndexed { i, allocation ->
                                val c = allocColors.getOrElse(i) { Color.Gray }
                                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                    Box(Modifier.width(8.dp).height(8.dp).background(c, CircleShape))
                                    Text(
                                        "${allocation.name} ${allocation.percentage?.let { "%.2f%%".format(Locale.CHINA, it) } ?: "比例未提供"}",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                        }

                        // 多段彩色条形进度
                        if (rendered.assetAllocation.all { it.percentage?.let { value -> value.isFinite() && value >= 0.0 } == true } &&
                            rendered.assetAllocation.sumOf { it.percentage ?: 0.0 } > 0.0) Box(Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp))) {
                            val allocColors = listOf(MaterialTheme.colorScheme.primary, WealthRiskYellow, WealthNegative, MaterialTheme.colorScheme.secondary)
                            Row(Modifier.fillMaxSize()) {
                                rendered.assetAllocation.forEachIndexed { i, allocation ->
                                    val weight = (allocation.percentage ?: 0.0).toFloat()
                                    val c = allocColors.getOrElse(i) { Color.Gray }
                                    if (weight > 0f) Box(Modifier.weight(weight).fillMaxSize().background(c))
                                }
                            }
                        }

                        Text("重仓前五项", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)

                        Row(
                            Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(4.dp)).padding(vertical = 6.dp, horizontal = 4.dp),
                        ) {
                            Text("标的名称 / 类别", Modifier.weight(2.0f), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Text("持仓占比", Modifier.weight(1.0f), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
                            Text("今日涨跌", Modifier.weight(1.0f), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.End)
                        }

                        rendered.topHoldings.take(5).forEachIndexed { index, holding ->
                            Row(
                                Modifier.fillMaxWidth().padding(vertical = 6.dp, horizontal = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Column(Modifier.weight(2.0f).then(if (index == 0) Modifier.testTag(UiTags.WEALTH_TOP_HOLDINGS) else Modifier)) {
                                    Text("${index + 1}. ${holding.name}", style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium)
                                    Text(
                                        buildString {
                                            holding.category?.let { append(it) }
                                            holding.code?.let { append(" · $it") }
                                        },
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                                Text(
                                    holding.percentage?.let { "%.2f%%".format(Locale.CHINA, it) } ?: "—",
                                    Modifier.weight(1.0f),
                                    style = MaterialTheme.typography.bodySmall,
                                    fontWeight = FontWeight.Bold,
                                    textAlign = TextAlign.Center,
                                )
                                val change = holding.dailyChangePct
                                Text(
                                    change?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "—",
                                    Modifier.weight(1.0f),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = returnColor(change),
                                    textAlign = TextAlign.End,
                                )
                            }
                        }
                    }
                }
            }

            // 7. 基金概况卡片
            BankCard(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            ) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("基金概况", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Text("更多资料 ›", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.secondary)
                    }
                    DetailRow("成立日期", rendered.establishedAt ?: "暂无")
                    DetailRow("产品规模", rendered.scaleBillion?.let { "%.2f 亿元".format(Locale.CHINA, it) } ?: "暂无")
                    DetailRow("基金类型", rendered.fundType ?: "${rendered.category ?: "理财"} · ${rendered.riskLevel}")
                    DetailRow("管理人", rendered.managerCompany ?: "暂无")
                    DetailRow("托管人", rendered.custodian ?: "未提供")
                    DetailRow("综合评级", rendered.institutionRating ?: "未提供")
                }
            }

            // 8. 基金公告与产品文件卡片
            BankCard(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            ) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(
                        Modifier.fillMaxWidth().clickable { notice(rendered.announcement ?: "公告内容未提供") }.padding(vertical = 6.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("基金公告与运作季报", style = MaterialTheme.typography.bodyMedium)
                        Text("›", color = MaterialTheme.colorScheme.outline)
                    }
                    Box(Modifier.fillMaxWidth().height(0.5.dp).background(MaterialTheme.colorScheme.outlineVariant))
                    Row(
                        Modifier.fillMaxWidth().padding(vertical = 6.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("产品文件暂未提供", style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }

            // 9. 交易规则卡片
            WealthTradingRules(rendered)

            // 10. 合规与披露 (含测试所需展开项)
            BankCard(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_DISCLOSURE),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            ) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("合规与披露", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    listOf(
                        "产品公告与运作季报" to (rendered.announcement ?: "暂无公告"),
                        "申赎规则" to "${wealthConfirmationText(rendered)} · ${rendered.redemptionRule ?: "赎回规则未提供"} · 费率${rendered.feeRate?.let { "%.2f%%".format(Locale.CHINA, it) } ?: "未提供"}",
                        "风险揭示书与法律文件" to (rendered.summary ?: "请阅读完整法律文件"),
                    ).forEachIndexed { index, (title, body) ->
                        DisclosureRow(
                            title = title,
                            body = body,
                            rowTestTag = if (index == 1) UiTags.WEALTH_DISCLOSURE_RULES else null,
                            bodyTestTag = if (index == 1) UiTags.WEALTH_DISCLOSURE_BODY else null,
                            alwaysShowBody = index == 1,
                        )
                    }
                }
            }

            // 11. 风险提示免责声明
            Text(
                "风险提示：本页面所有行情、排名、持仓与指标均为 Mock 演示数据，仅供金融科技系统演示使用，不构成投资建议。" +
                    "业绩比较基准不代表未来收益承诺，理财产品过往业绩不代表未来表现，理财非存款、产品有风险、投资须谨慎。" +
                    "申购需通过风险测评并完成服务端多因子验证。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag(UiTags.WEALTH_DISCLAIMER).padding(horizontal = 4.dp, vertical = 8.dp),
            )

            Spacer(Modifier.height(40.dp))
        }
    }
}

internal fun wealthPurchaseFeeText(product: ProductView): String {
    val current = product.discountFeeRate?.takeIf { it.isFinite() && it >= 0.0 }
    val original = product.originalFeeRate?.takeIf { it.isFinite() && it >= 0.0 }
    return "买入费率${current?.let { " %.2f%%".format(Locale.CHINA, it) } ?: "未提供"}" +
        (original?.let { " · 原费率 %.2f%%".format(Locale.CHINA, it) } ?: "")
}

internal fun wealthConfirmationText(product: ProductView): String =
    product.confirmTime?.takeIf { it.isNotBlank() }?.let { "买入确认$it" } ?: "买入确认时间未提供"

internal fun wealthManagerFacts(product: ProductView): String = buildList {
    product.manager?.experienceYears?.takeIf { it.isFinite() && it >= 0.0 }?.let { add("从业%.1f年".format(Locale.CHINA, it)) }
    product.manager?.managedScaleBillion?.takeIf { it.isFinite() && it >= 0.0 }?.let { add("在管规模%.1f亿元".format(Locale.CHINA, it)) }
}.joinToString(" · ").ifEmpty { "从业年限与在管规模未提供" }

internal fun wealthInstitutionFacts(product: ProductView): String = buildList {
    add("管理人：${product.managerCompany?.takeIf { it.isNotBlank() } ?: "未提供"}")
    product.institutionFoundedYears?.takeIf { it >= 0 }?.let { add("成立${it}年") }
    product.institutionAumBillion?.takeIf { it.isFinite() && it >= 0.0 }?.let { add("管理规模${it}亿元") }
    product.institutionRating?.takeIf { it.isNotBlank() }?.let(::add)
}.joinToString(" · ")

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun WealthTradingRules(product: ProductView) {
    BankCard(
        modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_RULES),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
    ) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("交易规则", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            FlowRow(
                Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(8.dp)).padding(10.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                TimelineStep("1", "提交申请", "核对产品金额")
                TimelineStep("2", "安全验证", "确认后办理")
                TimelineStep("3", "查看回执", "核对实际结果")
            }
            DetailRow("买入规则", product.purchaseRule ?: "买入规则未提供")
            DetailRow("赎回规则", product.redemptionRule ?: "赎回规则未提供")
            DetailRow("确认时间", wealthConfirmationText(product))
            DetailRow("买入费率", wealthPurchaseFeeText(product))
            Text("以上为产品资料，办理状态与到账结果以实际回执为准。", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun WealthSummary(product: ProductView, onPromo: () -> Unit) {
    BankCard(
        modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_SUMMARY),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                CategoryChip(product.category ?: "理财产品")
                RiskBadge(product.riskLevel)
                CategoryChip(product.term ?: "期限未提供")
            }
            Column(Modifier.fillMaxWidth().testTag(UiTags.WEALTH_SNAPSHOT), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                val displayReturn = product.annualReturnPct ?: product.annualizedYield?.let { it * 100 }
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(if (product.annualReturnPct != null) "近1年收益率" else "业绩比较基准", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(displayReturn?.let { "%+.2f%%".format(Locale.CHINA, it).removePrefix("+") } ?: "—", fontSize = 42.sp, fontWeight = FontWeight.SemiBold, color = returnColor(displayReturn))
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                    val daily = product.dailyChangePct
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("日增长率", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(daily?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "—", style = MaterialTheme.typography.titleLarge, color = returnColor(daily), fontWeight = FontWeight.SemiBold)
                    }
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("单位净值", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(product.latestNav?.let { "%.4f".format(Locale.CHINA, it) } ?: "—", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                    }
                }
            }
            Text("%,.0f元起购 · %s".format(Locale.CHINA, product.minAmount, product.term ?: "以说明书为准"), style = MaterialTheme.typography.bodyMedium)
            Text("净值日期 ${product.netValueDate ?: product.asOfDate ?: "—"} · 数据来源：${product.dataSource ?: "MOCK_FIXTURE（非实时）"}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Surface(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_PROMO).clip(RoundedCornerShape(16.dp))
                    .then(if (!product.promoTitle.isNullOrBlank() || !product.promoSubtitle.isNullOrBlank()) Modifier.clickable(onClick = onPromo) else Modifier),
                color = MaterialTheme.colorScheme.tertiaryContainer,
                shape = RoundedCornerShape(16.dp),
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(product.ratingAgency ?: "评级信息未提供", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onTertiaryContainer, fontWeight = FontWeight.SemiBold)
                    Text(listOf(product.awardTitle, product.promoTitle, product.promoSubtitle).firstOrNull { !it.isNullOrBlank() }
                        ?: "优惠信息未提供", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onTertiaryContainer)
                    Text(wealthPurchaseFeeText(product), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onTertiaryContainer)
                }
            }
        }
    }
}

@Composable
private fun TimelineStep(step: String, title: String, subtitle: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            Modifier.background(MaterialTheme.colorScheme.primaryContainer, CircleShape).padding(horizontal = 8.dp, vertical = 2.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text(step, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.secondary)
        }
        Spacer(Modifier.height(4.dp))
        Text(title, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium)
        Text(subtitle, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun DisclosureRow(
    title: String,
    body: String,
    rowTestTag: String?,
    bodyTestTag: String?,
    alwaysShowBody: Boolean = false,
) {
    var expanded by remember(title) { mutableStateOf(false) }
    Column(Modifier.animateContentSize()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(8.dp))
                .clickable { expanded = !expanded }
                .heightIn(min = 48.dp)
                .then(rowTestTag?.let { Modifier.testTag(it) } ?: Modifier)
                .padding(horizontal = 8.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(title, style = MaterialTheme.typography.bodyMedium)
            Text(if (expanded) "⌄" else "›", color = MaterialTheme.colorScheme.outline)
        }
        if (expanded || alwaysShowBody) {
            Text(
                body,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = (bodyTestTag?.let { Modifier.testTag(it) } ?: Modifier)
                    .padding(horizontal = 8.dp, vertical = 4.dp),
            )
        }
    }
}

@Composable
private fun SectionCard(
    title: String,
    modifier: Modifier = Modifier,
    headerAction: (@Composable () -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    BankSectionCard(modifier = modifier) {
            BankSectionHeader(title = title, trailing = headerAction)
            Column(verticalArrangement = Arrangement.spacedBy(BankSpacing.xs), content = content)
    }
}

@Composable
private fun Metric(value: String, label: String, color: Color) {
    Column(Modifier.width(96.dp)) {
        Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, color = color)
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun WealthChart(product: ProductView, range: String, modifier: Modifier = Modifier) {
    val semantic = LocalBankSemanticColors.current
    val chartSurface = MaterialTheme.colorScheme.surface
    val all = product.navSeries.filter { it.nav?.isFinite() == true && it.benchmarkNav?.isFinite() == true }
    val points = rangePoints(all, range)
    var selectedIndex by remember(points) { mutableStateOf(points.lastIndex.coerceAtLeast(0)) }

    Column(
        modifier.semantics {
            contentDescription = if (points.isEmpty()) "业绩走势图暂无数据" else {
                val point = points[selectedIndex.coerceIn(0, points.lastIndex)]
                "业绩走势图，区间 $range，日期 ${point.date}，产品净值 ${point.nav ?: 0.0}，基准净值 ${point.benchmarkNav ?: 0.0}"
            }
        },
    ) {
        if (points.isEmpty()) {
            Box(
                Modifier.fillMaxWidth().height(160.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(16.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Text("业绩走势图暂无数据", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            return@Column
        }
        if (points.isNotEmpty()) {
            val point = points[selectedIndex.coerceIn(0, points.lastIndex)]
            Row(
                Modifier.fillMaxWidth().padding(bottom = 6.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    "${point.date} · 净值 ${point.nav?.let { "%.4f".format(Locale.CHINA, it) } ?: "—"}",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = semantic.chartPrimary,
                )
                Text(
                    "基准 ${point.benchmarkNav?.let { "%.4f".format(Locale.CHINA, it) } ?: "—"}",
                    style = MaterialTheme.typography.labelSmall,
                    color = semantic.chartBenchmark,
                )
            }
        }
        Canvas(
            Modifier
                .fillMaxWidth()
                .height(160.dp)
                .pointerInput(points) {
                    detectHorizontalDragGestures { change, _ ->
                        change.consume()
                        if (points.size > 1) {
                            selectedIndex = ((change.position.x / size.width) * (points.size - 1)).toInt().coerceIn(0, points.lastIndex)
                        }
                    }
                }
        ) {
            if (points.size < 2) return@Canvas
            repeat(4) { line ->
                val gridY = size.height * (line + 1) / 5f
                drawLine(semantic.chartGrid.copy(alpha = .55f), Offset(0f, gridY), Offset(size.width, gridY), strokeWidth = 1.dp.toPx())
            }
            val min = points.minOf { minOf(it.nav!!, it.benchmarkNav!!) }
            val max = points.maxOf { maxOf(it.nav!!, it.benchmarkNav!!) }
            val span = (max - min).takeIf { it > 0.0 } ?: 1.0
            fun x(i: Int) = i * size.width / (points.size - 1)
            fun y(value: Double) = size.height * 0.85f - ((value - min) / span * (size.height * 0.70f)).toFloat()

            // 绘制产品曲线下的浅红渐变遮罩
            val fillPath = Path().apply {
                moveTo(x(0), size.height)
                lineTo(x(0), y(points[0].nav!!))
                for (i in 1 until points.size) {
                    lineTo(x(i), y(points[i].nav!!))
                }
                lineTo(x(points.lastIndex), size.height)
                close()
            }
            drawPath(
                fillPath,
                Brush.verticalGradient(
                    colors = listOf(semantic.chartPrimary.copy(alpha = 0.17f), semantic.chartPrimary.copy(alpha = 0.01f)),
                    startY = y(max),
                    endY = size.height,
                ),
            )

            // 绘制基准曲线 (灰蓝色)
            for (i in 0 until points.lastIndex) {
                drawLine(
                    semantic.chartBenchmark,
                    Offset(x(i), y(points[i].benchmarkNav!!)),
                    Offset(x(i + 1), y(points[i + 1].benchmarkNav!!)),
                    strokeWidth = 3f,
                    cap = StrokeCap.Round,
                )
            }

            // 绘制本产品曲线 (醒目红)
            for (i in 0 until points.lastIndex) {
                drawLine(
                    semantic.chartPrimary,
                    Offset(x(i), y(points[i].nav!!)),
                    Offset(x(i + 1), y(points[i + 1].nav!!)),
                    strokeWidth = 5f,
                    cap = StrokeCap.Round,
                )
            }

            // 最高点标记与虚线指示
            val maxNavIndex = points.indices.maxByOrNull { points[it].nav ?: 0.0 } ?: 0
            val peakX = x(maxNavIndex)
            val peakY = y(points[maxNavIndex].nav!!)
            drawLine(
                semantic.chartPrimary.copy(alpha = 0.5f),
                Offset(peakX, 10f),
                Offset(peakX, peakY),
                strokeWidth = 2f,
            )
            drawCircle(semantic.chartPrimary, 5f, Offset(peakX, peakY))

            // 交互十字准星
            val index = selectedIndex.coerceIn(0, points.lastIndex)
            val scrubX = x(index)
            drawLine(semantic.chartGrid, Offset(scrubX, 0f), Offset(scrubX, size.height), strokeWidth = 2f)
            drawCircle(chartSurface, 7f, Offset(scrubX, y(points[index].nav!!)))
            drawCircle(semantic.chartPrimary, 5f, Offset(scrubX, y(points[index].nav!!)))
            drawCircle(chartSurface, 6f, Offset(scrubX, y(points[index].benchmarkNav!!)))
            drawCircle(semantic.chartBenchmark, 4f, Offset(scrubX, y(points[index].benchmarkNav!!)))
        }
    }
}

private fun rangePoints(points: List<com.aibanking.agent.data.NavPointView>, range: String): List<com.aibanking.agent.data.NavPointView> {
    if (points.isEmpty()) return emptyList()
    val parsed = points.mapNotNull { point ->
        runCatching { LocalDate.parse(point.date) to point }.getOrNull()
    }
    if (parsed.size != points.size) {
        val fallbackCount = when (range) {
            "近1月" -> 5
            "近3月" -> 10
            else -> points.size
        }.coerceAtLeast(1)
        return points.takeLast(fallbackCount)
    }
    val end = parsed.maxOf { it.first }
    val start = when (range) {
        "近1月" -> end.minusMonths(1)
        "近3月" -> end.minusMonths(3)
        "近6月" -> end.minusMonths(6)
        "近1年" -> end.minusMonths(12)
        "成立以来" -> LocalDate.MIN
        else -> end.minusMonths(12)
    }
    return parsed.filter { it.first >= start && it.first <= end }.map { it.second }.ifEmpty { points.takeLast(1) }
}

@Composable
private fun returnColor(value: Double?): Color = when {
    value == null || !value.isFinite() || value == 0.0 -> Color.Unspecified
    value > 0.0 -> WealthPositive
    else -> WealthNegative
}

@Composable
private fun PurchaseBar(state: ChatUiState, viewModel: ChatViewModel, product: ProductView) {
    val profile = state.riskProfile
    val eligible = profile != null && (profileRank(profile.riskProfile) ?: 0) >= productRank(product.riskLevel)
    Surface(
        shadowElevation = 4.dp,
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp),
    ) {
        Column(
            Modifier.fillMaxWidth().navigationBarsPadding().imePadding().padding(horizontal = 20.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    wealthPurchaseFeeText(product),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.weight(1f),
                )
                BankTextButton(onClick = {
                    viewModel.onInputChange("推荐适合我的理财产品")
                    viewModel.closeWealth()
                }, contentPadding = PaddingValues(horizontal = 10.dp)) { Text("查看推荐", style = MaterialTheme.typography.labelMedium) }
            }
            if (eligible) {
                OutlinedTextField(
                    value = state.buyAmount,
                    onValueChange = viewModel::onBuyAmountChange,
                    label = { Text("申购金额") },
                    placeholder = { Text("起购 ¥%,.0f".format(Locale.CHINA, product.minAmount)) },
                    prefix = { Text("¥") },
                    enabled = !state.panelBusy,
                    colors = bankTextFieldColors(),
                    singleLine = true,
                    shape = RoundedCornerShape(16.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_AMOUNT),
                )
            } else {
                Text(
                    if (profile == null) "购买前先完成风险测评" else "当前风险档案与本产品不匹配，请重新测评",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Row(
                Modifier.fillMaxWidth().testTag(UiTags.WEALTH_BOTTOM_BAR),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                BankOutlinedButton(
                    onClick = viewModel::backToWealthList,
                    modifier = Modifier.weight(1f).heightIn(min = 52.dp),
                    shape = RoundedCornerShape(16.dp),
                ) { Text("查看持仓") }
                if (eligible) {
                    BankButton(
                        onClick = { viewModel.startPurchase(product, state.buyAmount) },
                        enabled = state.canBuySelected && !state.panelBusy,
                        modifier = Modifier.weight(1.2f).heightIn(min = 52.dp).testTag(UiTags.WEALTH_BUY),
                        shape = RoundedCornerShape(16.dp),
                    ) { Text(if (state.panelBusy) "处理中…" else "发起申购") }
                } else {
                    BankButton(
                        onClick = { viewModel.onInputChange("我想做风险测评"); viewModel.closeWealth() },
                        modifier = Modifier.weight(1.2f).heightIn(min = 52.dp).testTag(UiTags.WEALTH_TO_TEST),
                        shape = RoundedCornerShape(16.dp),
                    ) { Text(if (profile == null) "先做风险测评" else "重新测评") }
                }
            }
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
private fun CategoryChip(text: String) {
    Box(
        Modifier
            .background(MaterialTheme.colorScheme.secondaryContainer, RoundedCornerShape(8.dp))
            .padding(horizontal = 10.dp, vertical = 6.dp),
    ) {
        Text(text, style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun RiskBadge(level: String) {
    val status = when (level) {
        "R1" -> BankSemanticStatus.RiskGreen
        "R2" -> BankSemanticStatus.Neutral
        "R3" -> BankSemanticStatus.RiskYellow
        else -> BankSemanticStatus.RiskRed
    }
    BankStatusChip(level, status)
}

@Composable
private fun DetailRow(label: String, value: String) {
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
