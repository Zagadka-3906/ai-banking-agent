package com.aibanking.agent.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.Button
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.shape.RoundedCornerShape
import com.aibanking.agent.ui.theme.BankSpacing
import com.aibanking.agent.ui.theme.LocalBankSemanticColors

@Composable
fun BankSurfaceContainer(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Surface(modifier = modifier.fillMaxWidth()) {
        Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.TopCenter) {
            Column(
                modifier = Modifier.widthIn(max = BankSpacing.contentMaxWidth).fillMaxWidth(),
                content = { content() },
            )
        }
    }
}

@Composable
fun BankSectionHeader(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    trailing: (@Composable () -> Unit)? = null,
) {
    Row(
        modifier = modifier.fillMaxWidth().heightIn(min = BankSpacing.minTouchTarget),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(BankSpacing.sm),
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (subtitle != null) Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        trailing?.invoke()
    }
}

@Composable
fun BankSectionCard(
    modifier: Modifier = Modifier,
    outlined: Boolean = false,
    content: @Composable () -> Unit,
) {
    val contentModifier = Modifier.fillMaxWidth().padding(BankSpacing.md)
    if (outlined) OutlinedCard(modifier = modifier.fillMaxWidth()) { Column(contentModifier) { content() } }
    else ElevatedCard(modifier = modifier.fillMaxWidth(), elevation = CardDefaults.elevatedCardElevation(defaultElevation = 2.dp)) { Column(contentModifier) { content() } }
}

@Composable
fun BankMetricCard(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    supporting: String? = null,
    valueColor: Color = MaterialTheme.colorScheme.onSurface,
) {
    BankSectionCard(modifier = modifier) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.headlineSmall, color = valueColor, maxLines = 1, overflow = TextOverflow.Ellipsis)
        if (supporting != null) Text(supporting, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

enum class BankSemanticStatus { Positive, Negative, Neutral, RiskGreen, RiskYellow, RiskRed }

@Composable
fun BankStatusChip(
    label: String,
    status: BankSemanticStatus,
    modifier: Modifier = Modifier,
    containerColor: Color? = null,
) {
    val colors = LocalBankSemanticColors.current
    val color = when (status) {
        BankSemanticStatus.Positive -> colors.positive
        BankSemanticStatus.Negative -> colors.negative
        BankSemanticStatus.Neutral -> colors.neutral
        BankSemanticStatus.RiskGreen -> colors.riskGreen
        BankSemanticStatus.RiskYellow -> colors.riskYellow
        BankSemanticStatus.RiskRed -> colors.riskRed
    }
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(50),
        color = containerColor ?: color.copy(alpha = 0.12f),
        contentColor = color,
    ) {
        Text(
            text = label,
            modifier = Modifier.padding(horizontal = BankSpacing.sm, vertical = BankSpacing.xxs),
            style = MaterialTheme.typography.labelMedium,
        )
    }
}

@Composable
fun BankActionRow(
    primaryLabel: String,
    onPrimary: () -> Unit,
    modifier: Modifier = Modifier,
    secondaryLabel: String? = null,
    onSecondary: (() -> Unit)? = null,
    primaryEnabled: Boolean = true,
) {
    Row(modifier = modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(BankSpacing.sm)) {
        if (secondaryLabel != null && onSecondary != null) OutlinedButton(onClick = onSecondary, modifier = Modifier.weight(1f).heightIn(min = BankSpacing.minTouchTarget)) { Text(secondaryLabel) }
        Button(onClick = onPrimary, enabled = primaryEnabled, modifier = Modifier.weight(1f).heightIn(min = BankSpacing.minTouchTarget)) { Text(primaryLabel) }
    }
}

@Composable
fun BankLoadingState(modifier: Modifier = Modifier, message: String = "正在加载…") {
    Column(modifier = modifier.fillMaxWidth().padding(BankSpacing.xl), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(BankSpacing.sm)) {
        CircularProgressIndicator()
        Text(message, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
fun BankEmptyState(message: String, modifier: Modifier = Modifier, actionLabel: String? = null, onAction: (() -> Unit)? = null) {
    Column(modifier = modifier.fillMaxWidth().padding(BankSpacing.xl), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(BankSpacing.sm)) {
        Text(message, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (actionLabel != null && onAction != null) TextButton(onClick = onAction, modifier = Modifier.heightIn(min = BankSpacing.minTouchTarget)) { Text(actionLabel) }
    }
}

@Composable
fun BankErrorState(message: String, modifier: Modifier = Modifier, onRetry: (() -> Unit)? = null) {
    Column(modifier = modifier.fillMaxWidth().padding(BankSpacing.xl), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(BankSpacing.sm)) {
        Text(message, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
        if (onRetry != null) OutlinedButton(onClick = onRetry, modifier = Modifier.heightIn(min = BankSpacing.minTouchTarget)) { Text("重试") }
    }
}

@Composable
fun BankIconTextRow(icon: @Composable () -> Unit, text: String, modifier: Modifier = Modifier, supporting: String? = null) {
    Row(modifier = modifier.fillMaxWidth().heightIn(min = BankSpacing.minTouchTarget), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(BankSpacing.sm)) {
        icon()
        Column(modifier = Modifier.weight(1f)) {
            Text(text, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (supporting != null) Text(supporting, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
