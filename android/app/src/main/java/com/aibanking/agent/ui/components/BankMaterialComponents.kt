package com.aibanking.agent.ui.components

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.isSystemInDarkTheme
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
import androidx.compose.foundation.text.selection.TextSelectionColors
import androidx.compose.material3.ButtonColors
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.TextFieldColors
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.isSpecified
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.anim.BankHaptics
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.anim.SkeletonBlock
import com.aibanking.agent.ui.theme.BankSpacing
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import kotlin.math.hypot
import kotlinx.coroutines.launch

/** Pastel brand fills stay on buttons; input labels and focus need stronger contrast. */
@Composable
fun bankTextFieldColors(): TextFieldColors {
    val colors = MaterialTheme.colorScheme
    val focus = LocalBankSemanticColors.current.inputFocus
    return OutlinedTextFieldDefaults.colors(
        focusedContainerColor = colors.surface,
        unfocusedContainerColor = colors.surface,
        disabledContainerColor = colors.surface,
        errorContainerColor = colors.surface,
        focusedBorderColor = focus,
        unfocusedBorderColor = colors.outlineVariant,
        focusedLabelColor = focus,
        cursorColor = focus,
        errorCursorColor = colors.error,
        selectionColors = TextSelectionColors(handleColor = focus, backgroundColor = focus.copy(alpha = 0.25f)),
    )
}

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
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    eyebrow: String? = null,
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

/** Filled section on the grey container; `outlined` adds a hairline for emphasis. */
@Composable
fun BankSectionCard(modifier: Modifier = Modifier, outlined: Boolean = false, containerColor: Color = MaterialTheme.colorScheme.surfaceContainer, content: @Composable () -> Unit) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        color = containerColor,
        border = if (outlined) BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant) else null,
    ) { Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) { content() } }
}

/** Soft blue-to-violet wash used for the few hero summaries. */
@Composable
fun BankHeroCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    CompositionLocalProvider(LocalContentColor provides MaterialTheme.colorScheme.onSurface) {
        Column(
            modifier.fillMaxWidth().clip(MaterialTheme.shapes.large)
                .background(LocalBankSemanticColors.current.primaryGradient).padding(20.dp),
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
    val semantic = LocalBankSemanticColors.current
    val color = when (status) {
        BankSemanticStatus.Positive -> semantic.riskRedContainer
        BankSemanticStatus.Negative -> semantic.riskGreenContainer
        BankSemanticStatus.Neutral -> MaterialTheme.colorScheme.surfaceContainerHighest
        BankSemanticStatus.RiskGreen -> semantic.riskGreenContainer
        BankSemanticStatus.RiskYellow -> semantic.riskYellowContainer
        BankSemanticStatus.RiskRed -> semantic.riskRedContainer
    }
    val content = if (containerColor != null || status == BankSemanticStatus.Neutral) MaterialTheme.colorScheme.onSurface else semantic.onRiskContainer
    Surface(modifier, shape = CircleShape, color = containerColor ?: color, contentColor = content) {
        Text(label, Modifier.padding(horizontal = 10.dp, vertical = 4.dp), style = MaterialTheme.typography.labelMedium)
    }
}

@Composable
fun BankPrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    loading: Boolean = false,
    colors: ButtonColors = ButtonDefaults.buttonColors(),
) {
    val interaction = remember { MutableInteractionSource() }
    BankButton(
        onClick = onClick,
        enabled = enabled && !loading,
        modifier = modifier.heightIn(min = 48.dp),
        interactionSource = interaction,
        shape = CircleShape,
        colors = colors,
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
        shape = CircleShape,
        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 20.dp, vertical = 14.dp),
    ) { Text(text, style = MaterialTheme.typography.labelLarge) }
}

/**
 * 黄区长按确认按钮：
 * 1. 按住时，从手指触碰的真实物理坐标 (x, y) 向四周展开液体扩散填充动画（周期 1000ms）。
 * 2. 蓄满指定时长后，填满整个胶囊按钮，触发成功触感反馈并执行确认回调。
 * 3. 中途松开或划出则取消执行，液体平滑倒流回缩并复位。
 * 4. 支持语义点击事件（Semantics onClick），在自动化测试与无障碍服务（TalkBack）中正常触发。
 */
@Composable
fun BankHoldToConfirmButton(
    text: String,
    onConfirmed: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    loading: Boolean = false,
    durationMillis: Long = 1000L,
) {
    val motion = LocalBankMotionEnabled.current
    val view = LocalView.current
    val isDark = isSystemInDarkTheme()
    val coroutineScope = rememberCoroutineScope()
    val fillProgress = remember { Animatable(0f) }
    var touchOrigin by remember { mutableStateOf(Offset.Unspecified) }

    val baseContainerColor = if (isDark) Color(0xFF382900) else Color(0xFFFFF3D0)
    val liquidColor = if (isDark) Color(0xFFFDD663) else Color(0xFFF9AB00)
    val borderColor = if (isDark) Color(0xFFFDD663).copy(alpha = 0.5f) else Color(0xFFF9AB00).copy(alpha = 0.7f)
    val contentColor = Color(0xFF1F1F1F)

    Box(
        modifier = modifier
            .heightIn(min = 48.dp)
            .clip(CircleShape)
            .background(if (enabled) baseContainerColor else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.12f))
            .border(
                width = 1.5.dp,
                color = if (enabled) borderColor else Color.Transparent,
                shape = CircleShape,
            )
            .semantics {
                role = Role.Button
                if (!enabled || loading) {
                    disabled()
                } else {
                    onClick(label = text) {
                        onConfirmed()
                        true
                    }
                }
            }
            .pointerInput(enabled, loading) {
                if (!enabled || loading) return@pointerInput
                awaitEachGesture {
                    val down = awaitFirstDown(requireUnconsumed = false)
                    touchOrigin = down.position
                    var confirmed = false
                    val animJob = coroutineScope.launch {
                        if (motion) {
                            BankHaptics.tick(view)
                            fillProgress.snapTo(0f)
                            fillProgress.animateTo(
                                targetValue = 1f,
                                animationSpec = tween(durationMillis = durationMillis.toInt(), easing = LinearEasing),
                            )
                        } else {
                            fillProgress.snapTo(1f)
                        }
                        confirmed = true
                        BankHaptics.confirm(view)
                        onConfirmed()
                    }

                    while (true) {
                        val event = awaitPointerEvent()
                        val change = event.changes.firstOrNull { it.id == down.id }
                        if (change == null || !change.pressed) break
                    }

                    if (!confirmed) {
                        animJob.cancel()
                        coroutineScope.launch {
                            fillProgress.animateTo(0f, tween(200, easing = FastOutSlowInEasing))
                        }
                    }
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        Canvas(modifier = Modifier.matchParentSize()) {
            val progress = fillProgress.value
            if (progress > 0f) {
                val origin = if (touchOrigin.isSpecified) touchOrigin else Offset(size.width / 2f, size.height / 2f)
                val maxRadius = hypot(
                    maxOf(origin.x, size.width - origin.x),
                    maxOf(origin.y, size.height - origin.y),
                )
                val waveRadius = (maxRadius * progress * 1.06f).coerceAtMost(maxRadius)
                drawCircle(
                    color = liquidColor.copy(alpha = 0.45f),
                    radius = waveRadius,
                    center = origin,
                )
                drawCircle(
                    color = liquidColor,
                    radius = maxRadius * progress,
                    center = origin,
                )
            }
        }

        Row(
            modifier = Modifier.padding(horizontal = 24.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (loading) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    color = contentColor,
                    strokeWidth = 2.dp,
                )
            } else {
                BankIcon(
                    kind = BankIconKind.Check,
                    modifier = Modifier.size(18.dp),
                    tint = if (enabled) contentColor else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
                )
            }
            val label = when {
                loading -> "正在办理..."
                fillProgress.value in 0.05f..0.99f -> "按住以确认..."
                else -> text
            }
            Text(
                text = label,
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
                color = if (enabled) contentColor else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
            )
        }
    }
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
        Surface(shape = CircleShape, color = MaterialTheme.colorScheme.surfaceContainerHigh) {
            BankIcon(BankIconKind.Wallet, Modifier.padding(18.dp).size(28.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
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
