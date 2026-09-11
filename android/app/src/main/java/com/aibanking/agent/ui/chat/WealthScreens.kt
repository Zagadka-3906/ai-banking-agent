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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.UiTags
import java.util.Locale

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
fun WealthUi(state: ChatUiState, viewModel: ChatViewModel) {
    val snackbar = remember { SnackbarHostState() }
    LaunchedEffect(state.error, state.notice) {
        val message = state.error ?: state.notice
        if (message != null) {
            snackbar.showSnackbar(message)
            viewModel.dismissMessages()
        }
    }
    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        when (state.wealthScreen) {
            WealthScreen.LIST -> WealthListScreen(state, viewModel, snackbar)
            WealthScreen.DETAIL -> WealthDetailScreen(state, viewModel, snackbar)
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
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        if (state.panelBusy && state.wealthProducts.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            return@Scaffold
        }
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item { RiskProfileBanner(state, viewModel) }
            item { SectionHeader("我的持仓") }
            if (state.wealthHoldings.isEmpty()) {
                item {
                    Text(
                        "暂无持仓。选择下方产品申购后，这里会展示持仓与本金。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                items(state.wealthHoldings.size) { index ->
                    val holding = state.wealthHoldings[index]
                    Card(Modifier.fillMaxWidth()) {
                        Row(
                            Modifier.fillMaxWidth().padding(12.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
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
            item { SectionHeader("在售产品") }
            items(state.wealthProducts.size) { index ->
                val product = state.wealthProducts[index]
                ProductCard(product) { viewModel.showProductDetail(product.productId) }
            }
            item {
                Text(
                    "业绩比较基准不代表未来收益承诺，理财非存款、产品有风险、投资须谨慎。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun ProductCard(product: ProductView, onClick: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.fillMaxWidth().padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                CategoryChip(product.category ?: "理财产品")
                RiskBadge(product.riskLevel)
            }
            Text(product.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text(
                product.benchmark ?: "业绩比较基准以产品说明书为准",
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.primary,
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
private fun RiskProfileBanner(state: ChatUiState, viewModel: ChatViewModel) {
    val profile = state.riskProfile
    Card(
        colors = CardDefaults.cardColors(
            containerColor = if (profile == null) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.primaryContainer,
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp),
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
            if (profile == null) {
                TextButton(onClick = { viewModel.onInputChange("我想做风险测评"); viewModel.closeWealth() }) { Text("去测评") }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// detail
// ---------------------------------------------------------------------------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun WealthDetailScreen(state: ChatUiState, viewModel: ChatViewModel, snackbar: SnackbarHostState) {
    val product = state.selectedProduct
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("产品详情") },
                navigationIcon = {
                    IconButton(onClick = viewModel::backToWealthList) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回列表") }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            if (product != null) {
                PurchaseBar(state, viewModel, product)
            }
        },
    ) { padding ->
        if (product == null) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                Text("产品不存在或已下架。", style = MaterialTheme.typography.bodyMedium)
            }
            return@Scaffold
        }
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                CategoryChip(product.category ?: "理财产品")
                RiskBadge(product.riskLevel)
            }
            Text(product.name, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
            Text(
                product.productId,
                style = MaterialTheme.typography.labelMedium,
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.fillMaxWidth().padding(14.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text("业绩比较基准", style = MaterialTheme.typography.labelMedium)
                    Text(
                        product.benchmark ?: "以产品说明书为准",
                        style = MaterialTheme.typography.titleLarge,
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.Bold,
                    )
                }
            }
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(vertical = 4.dp)) {
                    DetailRow("产品类型", product.category ?: "—")
                    DetailRow("风险等级", product.riskLevel)
                    DetailRow("投资期限", product.term ?: "—")
                    DetailRow("起购金额", "¥%,.0f".format(Locale.CHINA, product.minAmount))
                    DetailRow("流动性", product.liquidity ?: "—")
                }
            }
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(vertical = 4.dp)) {
                    DetailRow("底层资产", product.underlying ?: "—")
                    DetailRow("目标客群", product.targetClients ?: "—")
                }
            }
            Text(
                "风险提示：业绩比较基准是管理人基于过往资料测算的预期目标，不代表未来收益承诺。" +
                    "理财产品过往业绩不代表未来表现，投资须谨慎。申购需通过风险测评并完成服务端多因子验证。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun PurchaseBar(state: ChatUiState, viewModel: ChatViewModel, product: ProductView) {
    val profile = state.riskProfile
    Surface(shadowElevation = 8.dp, color = MaterialTheme.colorScheme.surface) {
        Column(Modifier.fillMaxWidth().padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            when {
                profile == null -> {
                    Text("申购前需完成风险测评问卷。", style = MaterialTheme.typography.bodySmall)
                    Button(
                        onClick = { viewModel.onInputChange("我想做风险测评"); viewModel.closeWealth() },
                        modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_TO_TEST),
                    ) { Text("去测评") }
                }
                (profileRank(profile.riskProfile) ?: 0) < productRank(product.riskLevel) -> {
                    Text(
                        "当前风险档案（${profileLabel(profile.riskProfile)}）不适合该产品的风险等级 ${product.riskLevel}。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
                else -> {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(
                            value = state.buyAmount,
                            onValueChange = viewModel::onBuyAmountChange,
                            label = { Text("申购金额（起购 ¥%,.0f）".format(Locale.CHINA, product.minAmount)) },
                            singleLine = true,
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            modifier = Modifier.weight(1f),
                        )
                        Button(
                            onClick = { viewModel.startPurchase(product, state.buyAmount) },
                            enabled = state.canBuySelected && !state.panelBusy,
                            modifier = Modifier.testTag(UiTags.WEALTH_BUY),
                        ) { Text("去申购") }
                    }
                    Text(
                        "点击后回到对话确认：申购属于高风险操作，需短信验证码与设备签名。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// shared pieces
// ---------------------------------------------------------------------------

@Composable
private fun SectionHeader(title: String) {
    Text(
        title,
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.Bold,
        modifier = Modifier.padding(top = 6.dp),
    )
}

@Composable
private fun CategoryChip(text: String) {
    Box(
        Modifier
            .background(MaterialTheme.colorScheme.secondaryContainer, RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    ) {
        Text(text, style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun RiskBadge(level: String) {
    val color = when (level) {
        "R1" -> Color(0xFF2E7D32)
        "R2" -> Color(0xFF1565C0)
        "R3" -> Color(0xFFE65100)
        else -> Color(0xFFC62828)
    }
    Box(
        Modifier
            .background(color, RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    ) {
        Text(level, style = MaterialTheme.typography.labelSmall, color = Color.White, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun DetailRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(96.dp),
        )
        Text(value, style = MaterialTheme.typography.bodyMedium)
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
