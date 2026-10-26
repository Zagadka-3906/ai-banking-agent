package com.aibanking.agent.ui.chat

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
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
import com.aibanking.agent.ui.anim.BankHaptics
import com.aibanking.agent.ui.components.BankButton
import com.aibanking.agent.ui.components.BankCard
import com.aibanking.agent.ui.components.BankFilterChip
import com.aibanking.agent.ui.components.BankOutlinedButton
import com.aibanking.agent.ui.components.BankTextButton
import com.aibanking.agent.ui.components.bankTextFieldColors
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import java.util.Locale
import kotlinx.coroutines.launch

// ---------------------------------------------------------------------------
// detail
// ---------------------------------------------------------------------------

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
internal fun WealthDetailScreen(state: ChatUiState, viewModel: ChatViewModel, snackbar: SnackbarHostState) {
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
                    WealthDetailRow("成立日期", rendered.establishedAt ?: "暂无")
                    WealthDetailRow("产品规模", rendered.scaleBillion?.let { "%.2f 亿元".format(Locale.CHINA, it) } ?: "暂无")
                    WealthDetailRow("基金类型", rendered.fundType ?: "${rendered.category ?: "理财"} · ${rendered.riskLevel}")
                    WealthDetailRow("管理人", rendered.managerCompany ?: "暂无")
                    WealthDetailRow("托管人", rendered.custodian ?: "未提供")
                    WealthDetailRow("综合评级", rendered.institutionRating ?: "未提供")
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
@Composable
private fun PurchaseBar(state: ChatUiState, viewModel: ChatViewModel, product: ProductView) {
    val profile = state.riskProfile
    val view = LocalView.current
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
                val liveSummary = remember(state.buyAmount, product) {
                    wealthLivePurchaseSummary(product, state.buyAmount)
                }
                OutlinedTextField(
                    value = state.buyAmount,
                    onValueChange = viewModel::onBuyAmountChange,
                    label = { Text("申购金额") },
                    placeholder = { Text("起购 ¥%,.0f".format(Locale.CHINA, product.minAmount)) },
                    prefix = { Text("¥") },
                    supportingText = if (liveSummary != null) {
                        {
                            Text(
                                liveSummary,
                                style = MaterialTheme.typography.labelSmall.copy(fontFeatureSettings = "tnum"),
                                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                            )
                        }
                    } else {
                        null
                    },
                    enabled = !state.panelBusy,
                    colors = bankTextFieldColors(),
                    singleLine = true,
                    shape = RoundedCornerShape(16.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_AMOUNT),
                )
                Row(
                    Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    BankFilterChip(
                        selected = false,
                        onClick = {
                            BankHaptics.tick(view)
                            viewModel.onBuyAmountChange(stepPurchaseAmount(state.buyAmount, -100.0, product.minAmount))
                        },
                        enabled = !state.panelBusy,
                        label = { Text("-100", style = MaterialTheme.typography.labelMedium.copy(fontFeatureSettings = "tnum")) },
                    )
                    BankFilterChip(
                        selected = false,
                        onClick = {
                            BankHaptics.tick(view)
                            viewModel.onBuyAmountChange("%.0f".format(Locale.ROOT, product.minAmount.coerceAtLeast(100.0)))
                        },
                        enabled = !state.panelBusy,
                        label = { Text("起购 ¥%,.0f".format(Locale.CHINA, product.minAmount.coerceAtLeast(100.0)), style = MaterialTheme.typography.labelMedium.copy(fontFeatureSettings = "tnum")) },
                    )
                    BankFilterChip(
                        selected = false,
                        onClick = {
                            BankHaptics.tick(view)
                            viewModel.onBuyAmountChange(stepPurchaseAmount(state.buyAmount, 100.0, product.minAmount))
                        },
                        enabled = !state.panelBusy,
                        label = { Text("+100", style = MaterialTheme.typography.labelMedium.copy(fontFeatureSettings = "tnum")) },
                    )
                    BankFilterChip(
                        selected = false,
                        onClick = {
                            BankHaptics.tick(view)
                            viewModel.onBuyAmountChange(stepPurchaseAmount(state.buyAmount, 1000.0, product.minAmount))
                        },
                        enabled = !state.panelBusy,
                        label = { Text("+1,000", style = MaterialTheme.typography.labelMedium.copy(fontFeatureSettings = "tnum")) },
                    )
                }
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

/**
 * Steps the purchase amount input by [delta] while respecting [minAmount] as a floor.
 */
internal fun stepPurchaseAmount(currentRaw: String, delta: Double, minAmount: Double): String {
    val floor = minAmount.coerceAtLeast(100.0)
    val current = parseAmountInput(currentRaw)?.toDouble() ?: floor
    val next = if (currentRaw.isBlank() && delta > 0) {
        floor + delta
    } else {
        (current + delta).coerceAtLeast(floor)
    }
    return if (next % 1.0 == 0.0) {
        "%.0f".format(Locale.ROOT, next)
    } else {
        "%.2f".format(Locale.ROOT, next)
    }
}

/**
 * Computes real-time linkage feedback for the purchase amount field:
 * - Shortfall message when > 0 and below [ProductView.minAmount]
 * - Estimated fee + confirmation rule when >= [ProductView.minAmount]
 */
internal fun wealthLivePurchaseSummary(product: ProductView, rawAmount: String): String? {
    if (rawAmount.isBlank()) return null
    val parsed = parseAmountInput(rawAmount)?.toDouble() ?: return "请输入合法的数字金额（最多两位小数）"
    if (parsed <= 0.0) return "申购金额需大于 0 元"
    if (parsed < product.minAmount) {
        val diff = (product.minAmount - parsed).coerceAtLeast(0.0)
        return "距起购金额还差 ¥%,.2f（起购 ¥%,.0f）".format(Locale.CHINA, diff, product.minAmount)
    }
    val ratePct = listOf(product.discountFeeRate, product.feeRate, product.originalFeeRate)
        .firstOrNull { it != null && it.isFinite() && it >= 0.0 }
    val feePart = if (ratePct != null) {
        val estFee = parsed * (ratePct / 100.0)
        "预估买入费用 ¥%,.2f".format(Locale.CHINA, estFee)
    } else {
        "买入费率以产品说明书为准"
    }
    return "$feePart · ${wealthConfirmationText(product)}"
}
