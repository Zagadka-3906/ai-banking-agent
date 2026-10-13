package com.aibanking.agent.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.anim.SkeletonBlock
import com.aibanking.agent.ui.theme.BankPalette
import com.aibanking.agent.ui.theme.BankSpacing

@Composable
fun BankSurfaceContainer(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Surface(modifier = modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.background) {
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.TopCenter) {
            Column(Modifier.widthIn(max = BankSpacing.contentMaxWidth).fillMaxWidth()) { content() }
        }
    }
}

@Composable
fun BankScreenHeading(
    title: String,
    subtitle: String? = null,
    eyebrow: String? = null,
    modifier: Modifier = Modifier,
    trailing: (@Composable () -> Unit)? = null,
) {
    Row(modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (eyebrow != null) BankSticker(eyebrow)
            Text(title, style = MaterialTheme.typography.headlineLarge, color = MaterialTheme.colorScheme.onBackground)
            if (subtitle != null) Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        trailing?.invoke()
    }
}

@Composable
fun BankSectionHeader(title: String, modifier: Modifier = Modifier, subtitle: String? = null, trailing: (@Composable () -> Unit)? = null) {
    Row(modifier.fillMaxWidth().heightIn(min = BankSpacing.minTouchTarget), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(BankSpacing.sm)) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (subtitle != null) Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        trailing?.invoke()
    }
}

@Composable
fun BankSectionCard(modifier: Modifier = Modifier, outlined: Boolean = false, containerColor: Color = MaterialTheme.colorScheme.surface, content: @Composable () -> Unit) {
    Surface(
        modifier = modifier.fillMaxWidth().bankHardShadow(MaterialTheme.shapes.large),
        shape = MaterialTheme.shapes.large,
        color = containerColor,
        border = BorderStroke(if (outlined) 2.dp else BankPalette.borderWidth, MaterialTheme.colorScheme.outline),
    ) { Column(Modifier.fillMaxWidth().padding(BankSpacing.md), verticalArrangement = Arrangement.spacedBy(10.dp)) { content() } }
}

/** The reference home hero uses wisteria with ink text in both themes. */
@Composable
fun BankHeroCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    CompositionLocalProvider(LocalContentColor provides BankPalette.Ink) {
        Column(
            modifier.fillMaxWidth().bankHardShadow(MaterialTheme.shapes.large)
                .clip(MaterialTheme.shapes.large).background(BankPalette.Wisteria)
                .then(Modifier.border(BankPalette.borderWidth, BankPalette.Ink, MaterialTheme.shapes.large)).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            content = content,
        )
    }
}

@Composable
fun BankMetricCard(label: String, value: String, modifier: Modifier = Modifier, supporting: String? = null, valueColor: Color = MaterialTheme.colorScheme.onSurface) {
    BankSectionCard(modifier) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.headlineSmall, color = valueColor, maxLines = 1, overflow = TextOverflow.Ellipsis)
        if (supporting != null) Text(supporting, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

enum class BankSemanticStatus { Positive, Negative, Neutral, RiskGreen, RiskYellow, RiskRed }

@Composable
fun BankStatusChip(label: String, status: BankSemanticStatus, modifier: Modifier = Modifier, containerColor: Color? = null) {
    val color = when (status) {
        BankSemanticStatus.Positive -> BankPalette.RedSoft
        BankSemanticStatus.Negative -> BankPalette.GreenSoft
        BankSemanticStatus.Neutral -> BankPalette.Neutral
        BankSemanticStatus.RiskGreen -> BankPalette.Green
        BankSemanticStatus.RiskYellow -> BankPalette.Yellow
        BankSemanticStatus.RiskRed -> BankPalette.Red
    }
    Surface(modifier, shape = RoundedCornerShape(6.dp), color = containerColor ?: color,
        contentColor = BankPalette.Ink, border = BorderStroke(BankPalette.borderWidth, BankPalette.Ink)) {
        Text(label, Modifier.padding(horizontal = 8.dp, vertical = 4.dp), style = MaterialTheme.typography.labelMedium)
    }
}

@Composable
fun BankPrimaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, loading: Boolean = false) {
    val interaction = remember { MutableInteractionSource() }
    BankButton(
        onClick = onClick,
        enabled = enabled && !loading,
        modifier = modifier.heightIn(min = 48.dp),
        interactionSource = interaction,
        shape = RoundedCornerShape(12.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 24.dp, vertical = 14.dp),
    ) {
        if (loading) {
            if (LocalBankMotionEnabled.current) CircularProgressIndicator(Modifier.size(18.dp), color = LocalContentColor.current, strokeWidth = 2.dp)
            else BankIcon(BankIconKind.More, Modifier.size(18.dp))
        }
        Text(text, modifier = if (loading) Modifier.padding(start = 10.dp) else Modifier, style = MaterialTheme.typography.labelLarge)
    }
}

@Composable
fun BankSecondaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    BankOutlinedButton(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.heightIn(min = 48.dp),
        shape = RoundedCornerShape(12.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 20.dp, vertical = 14.dp),
    ) { Text(text, style = MaterialTheme.typography.labelLarge) }
}

@Composable
fun BankActionRow(primaryLabel: String, onPrimary: () -> Unit, modifier: Modifier = Modifier, secondaryLabel: String? = null, onSecondary: (() -> Unit)? = null, primaryEnabled: Boolean = true) {
    Row(modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        if (secondaryLabel != null && onSecondary != null) BankSecondaryButton(secondaryLabel, onSecondary, Modifier.weight(1f))
        BankPrimaryButton(primaryLabel, onPrimary, Modifier.weight(1f), primaryEnabled)
    }
}

@Composable
fun BankLoadingState(modifier: Modifier = Modifier, message: String = "正在加载…") {
    Column(modifier.fillMaxWidth().padding(BankSpacing.xl).semantics { liveRegion = LiveRegionMode.Polite }, horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(16.dp)) {
        SkeletonBlock(height = 8.dp, width = 96.dp, corner = 4.dp)
        Text(message, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
fun BankEmptyState(message: String, modifier: Modifier = Modifier, actionLabel: String? = null, onAction: (() -> Unit)? = null) {
    Column(modifier.fillMaxWidth().padding(28.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer) {
            BankIcon(BankIconKind.Wallet, Modifier.padding(18.dp).size(28.dp), tint = MaterialTheme.colorScheme.onPrimaryContainer)
        }
        Text(message, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
        if (actionLabel != null && onAction != null) BankSecondaryButton(actionLabel, onAction)
    }
}

@Composable
fun BankErrorState(message: String, modifier: Modifier = Modifier, onRetry: (() -> Unit)? = null) {
    Column(modifier.fillMaxWidth().padding(24.dp).semantics { liveRegion = LiveRegionMode.Polite }, horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Surface(shape = CircleShape, color = MaterialTheme.colorScheme.errorContainer) {
            BankIcon(BankIconKind.Shield, Modifier.padding(16.dp).size(24.dp), tint = MaterialTheme.colorScheme.onErrorContainer)
        }
        Text(message, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
        if (onRetry != null) BankSecondaryButton("重新加载", onRetry)
    }
}

@Composable
fun BankIconTextRow(icon: @Composable () -> Unit, text: String, modifier: Modifier = Modifier, supporting: String? = null) {
    Row(modifier.fillMaxWidth().heightIn(min = 56.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
        icon()
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(text, style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (supporting != null) Text(supporting, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
