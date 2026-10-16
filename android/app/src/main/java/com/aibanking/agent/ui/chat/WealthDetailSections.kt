package com.aibanking.agent.ui.chat

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.components.BankCard
import com.aibanking.agent.ui.components.BankSectionCard
import com.aibanking.agent.ui.components.BankSectionHeader
import com.aibanking.agent.ui.theme.BankSpacing
import java.util.Locale

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
            WealthDetailRow("买入规则", product.purchaseRule ?: "买入规则未提供")
            WealthDetailRow("赎回规则", product.redemptionRule ?: "赎回规则未提供")
            WealthDetailRow("确认时间", wealthConfirmationText(product))
            WealthDetailRow("买入费率", wealthPurchaseFeeText(product))
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
internal fun DisclosureRow(
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
