package com.aibanking.agent.ui.chat

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.components.*
import com.aibanking.agent.ui.theme.BankPalette
import java.util.Locale
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun WealthMarketplace(
    state: ChatUiState,
    onClose: () -> Unit,
    onProduct: (String) -> Unit,
    onGroup: (String?) -> Unit,
    onAssess: () -> Unit,
    onRefresh: () -> Unit,
) {
    val group = WealthMarketGroups.firstOrNull { it.id == state.wealthMarketGroupId }
    var query by rememberSaveable { mutableStateOf("") }
    var filter by rememberSaveable(group?.id) { mutableStateOf("精选") }
    var holdingsExpanded by rememberSaveable { mutableStateOf(false) }
    val scroll = rememberLazyListState()
    val scope = rememberCoroutineScope()
    fun openGroup(id: String?) {
        query = ""
        onGroup(id)
        scope.launch { scroll.scrollToItem(0) }
    }
    fun back() { if (query.isNotBlank()) query = "" else if (group != null) openGroup(null) else onClose() }
    BackHandler(enabled = state.wealthScreen == WealthScreen.LIST && (group != null || query.isNotBlank())) { back() }
    Scaffold(topBar = {
        TopAppBar(
            title = { Column {
                Text(group?.title ?: "理财", style = MaterialTheme.typography.titleLarge)
                Text("虚构演示产品 · ${state.wealthProducts.size} 款在售", style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            } },
            navigationIcon = { IconButton(onClick = ::back) {
                BankIcon(BankIconKind.ArrowBack, contentDescription = if (group == null) "返回聊天" else "返回理财首页")
            } },
            actions = { IconButton(onClick = onRefresh, enabled = !state.panelBusy, modifier = Modifier.testTag("wealth_refresh")) {
                BankIcon(BankIconKind.Refresh, contentDescription = "刷新理财")
            } },
        )
    }) { padding ->
        if (state.panelBusy && state.wealthProducts.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        } else if (state.panelError != null && state.wealthProducts.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                PanelLoadFailure(state.panelError, onRefresh)
            }
        } else {
            LazyColumn(
                state = scroll, modifier = Modifier.fillMaxSize().padding(padding).testTag(UiTags.WEALTH_LIST),
                contentPadding = PaddingValues(16.dp, 12.dp, 16.dp, 32.dp), verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                if (state.panelError != null) item { Text("刷新未成功，当前显示上次加载的数据。", color = MaterialTheme.colorScheme.error) }
                if (group == null && query.isBlank()) item(key = "assets") {
                    Surface(shape = RoundedCornerShape(24.dp), color = BankPalette.Ink, contentColor = Color.White) {
                        Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                BankBrandMark(Modifier.size(22.dp), BankPalette.Gold)
                                Text("我的理财", style = MaterialTheme.typography.titleSmall)
                                Spacer(Modifier.weight(1f))
                                Text("虚构账本", style = MaterialTheme.typography.labelSmall, color = BankPalette.Gold)
                            }
                            Row(verticalAlignment = Alignment.Bottom) {
                                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                    Text("持仓本金", style = MaterialTheme.typography.labelMedium, color = Color.White.copy(alpha = .75f))
                                    Text("¥%,.2f".format(Locale.CHINA, state.wealthHoldings.sumOf { it.principal }),
                                        style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
                                }
                                TextButton(onClick = { holdingsExpanded = !holdingsExpanded },
                                    colors = ButtonDefaults.textButtonColors(contentColor = BankPalette.Gold),
                                    modifier = Modifier.testTag("wealth_holdings_toggle")) {
                                    Text("我的持仓 ${state.wealthHoldings.size} 笔")
                                }
                            }
                            AnimatedVisibility(holdingsExpanded) {
                                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                                    if (state.wealthHoldings.isEmpty()) Text("暂无持仓。浏览产品不会产生申购。", style = MaterialTheme.typography.bodySmall)
                                    state.wealthHoldings.forEach { holding ->
                                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                            Text(holding.productName, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                                            Text("¥%,.2f".format(Locale.CHINA, holding.principal), style = MaterialTheme.typography.bodySmall)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                item(key = "profile") {
                    Surface(shape = RoundedCornerShape(18.dp), color = MaterialTheme.colorScheme.surfaceContainer) {
                        Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            BankIcon(BankIconKind.Shield, Modifier.size(24.dp))
                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Text(state.riskProfile?.let { "风险档案 · ${profileLabel(it.riskProfile)}" } ?: "先了解自己的风险承受能力",
                                    style = MaterialTheme.typography.titleSmall)
                                Text(if (state.riskProfile == null) "浏览无需测评，申购前需要完成" else "以本人测评与产品匹配结果为准",
                                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            BankTextButton(onClick = onAssess) { Text(if (state.riskProfile == null) "去测评" else "重测") }
                        }
                    }
                }
                item(key = "search") {
                    OutlinedTextField(value = query, onValueChange = { query = it }, singleLine = true,
                        modifier = Modifier.fillMaxWidth().testTag("wealth_search"), shape = RoundedCornerShape(18.dp),
                        colors = bankTextFieldColors(), placeholder = { Text("搜索产品名称、代码或策略") },
                        leadingIcon = { BankIcon(BankIconKind.Search) },
                        trailingIcon = { if (query.isNotEmpty()) IconButton(onClick = { query = "" }) {
                            BankIcon(BankIconKind.Close, contentDescription = "清空产品搜索")
                        } })
                }
                item(key = "groups") {
                    LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        item { BankFilterChip(selected = group == null, onClick = { openGroup(null) }, label = { Text("全部") }) }
                        items(WealthMarketGroups, key = { it.id }) { section ->
                            BankFilterChip(selected = group?.id == section.id, onClick = { openGroup(section.id) }, label = { Text(section.title) },
                                modifier = Modifier.testTag("wealth_group_${section.id}"))
                        }
                    }
                }
                if (group != null || query.isNotBlank()) {
                    if (group != null) item(key = "filters") {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text(group.description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            WealthFilterRow(group, filter) { filter = it }
                        }
                    }
                    val matches = filterWealthProducts(state.wealthProducts, group, filter, query)
                    item(key = "count") { Text("找到 ${matches.size} 款产品", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    if (matches.isEmpty()) item { WealthEmptyFilter() }
                    items(matches, key = { it.productId }) { product ->
                        Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surfaceContainerLowest) {
                            WealthMarketProductRow(product, onProduct)
                        }
                    }
                } else {
                    items(WealthMarketGroups, key = { it.id }) { section ->
                        WealthCategorySection(section, state.wealthProducts, onProduct = onProduct, onMore = { openGroup(section.id) })
                    }
                }
                item(key = "notice") {
                    Text("产品、机构及业绩数据均为虚构演示。理财非存款，业绩参考不是收益承诺；申购仍需风险匹配、确认和验证。",
                        style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.testTag("wealth_market_notice"))
                }
            }
        }
    }
}

@Composable
private fun WealthFilterRow(group: WealthMarketGroup, selected: String, onSelect: (String) -> Unit) {
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        group.filters.forEach { filter ->
            BankFilterChip(selected = selected == filter, onClick = { onSelect(filter) }, label = { Text(filter) },
                modifier = Modifier.testTag("wealth_filter_${group.id}_$filter"))
        }
    }
}

@Composable
private fun WealthEmptyFilter() {
    Text("这个条件下暂无产品，试试其他期限、策略或关键词。", Modifier.padding(16.dp),
        style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

@Composable
internal fun WealthCategorySection(
    group: WealthMarketGroup, products: List<ProductView>, onProduct: (String) -> Unit,
    onMore: (() -> Unit)? = null, compactReply: Boolean = false,
) {
    var selected by rememberSaveable(group.id) { mutableStateOf("精选") }
    val candidates = filterWealthProducts(products, group, selected)
    val total = products.count { wealthMarketGroup(it).id == group.id }
    if (total == 0) return
    Surface(shape = RoundedCornerShape(24.dp), color = MaterialTheme.colorScheme.surfaceContainerLowest,
        modifier = Modifier.fillMaxWidth().testTag("wealth_section_${group.id}")) {
        Column(Modifier.padding(vertical = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(group.title, Modifier.weight(1f), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    if (onMore != null) BankTextButton(onClick = onMore, modifier = Modifier.testTag("wealth_more_${group.id}")) {
                        Text("更多 · $total")
                    } else Text("$total 款", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Text(group.description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (!compactReply && group.filters.size > 1) Box(Modifier.padding(horizontal = 16.dp)) { WealthFilterRow(group, selected) { selected = it } }
            if (candidates.isEmpty()) WealthEmptyFilter()
            else if (group.compact && !compactReply) {
                LazyRow(contentPadding = PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    items(candidates.take(4), key = { it.productId }) { product -> WealthCompactProduct(product, onProduct) }
                }
            } else {
                candidates.take(if (compactReply) 2 else 4).forEachIndexed { index, product ->
                    if (index > 0) HorizontalDivider(Modifier.padding(horizontal = 16.dp), color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = .5f))
                    WealthMarketProductRow(product, onProduct)
                }
                if (candidates.size > (if (compactReply) 2 else 4)) Text("更多产品可在理财频道查看", Modifier.padding(horizontal = 16.dp),
                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun WealthMarketProductRow(product: ProductView, onProduct: (String) -> Unit) {
    val metric = wealthDisplayMetric(product)
    Column(Modifier.fillMaxWidth().testTag(UiTags.wealthProduct(product.productId))
        .clickable(role = Role.Button, onClickLabel = "查看${product.name}详情") { onProduct(product.productId) }.padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(product.name, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Medium, maxLines = 2, overflow = TextOverflow.Ellipsis)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            product.displayTags.orEmpty().take(2).forEach { tag ->
                Surface(shape = RoundedCornerShape(5.dp), color = MaterialTheme.colorScheme.secondaryContainer) {
                    Text(tag, Modifier.padding(horizontal = 6.dp, vertical = 3.dp), style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSecondaryContainer)
                }
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(Modifier.widthIn(min = 120.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(metric.first, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold,
                    color = if (metric.first.startsWith("-")) WealthNegative else if (metric.first == "—") MaterialTheme.colorScheme.onSurface else WealthPositive)
                Text(metric.second, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Column(Modifier.widthIn(min = 130.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                Text(product.liquidity?.takeIf { it.isNotBlank() } ?: "期限未提供", style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.secondary, fontWeight = FontWeight.Medium)
                Text(wealthRiskLabel(product.riskLevel), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Text(if (product.minAmount > 0) "¥%,.0f 起购 · %s".format(Locale.CHINA, product.minAmount, product.productId) else "起购金额未提供 · ${product.productId}",
            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun WealthCompactProduct(product: ProductView, onProduct: (String) -> Unit) {
    val metric = wealthDisplayMetric(product)
    Column(Modifier.width(148.dp).testTag(UiTags.wealthProduct(product.productId))
        .clickable(role = Role.Button, onClickLabel = "查看${product.name}详情") { onProduct(product.productId) }.padding(4.dp),
        verticalArrangement = Arrangement.spacedBy(9.dp)) {
        Text(product.name, style = MaterialTheme.typography.titleSmall, minLines = 2, maxLines = 2, overflow = TextOverflow.Ellipsis)
        Text(wealthRiskLabel(product.riskLevel), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(metric.first, style = MaterialTheme.typography.headlineSmall,
            color = if (metric.first.startsWith("-")) WealthNegative else if (metric.first == "—") MaterialTheme.colorScheme.onSurface else WealthPositive,
            fontWeight = FontWeight.SemiBold)
        Text(metric.second, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(product.liquidity ?: "期限未提供", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.secondary)
        Text("¥%,.0f 起购".format(Locale.CHINA, product.minAmount), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
