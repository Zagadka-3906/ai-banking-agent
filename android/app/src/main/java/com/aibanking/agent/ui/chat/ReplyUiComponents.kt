package com.aibanking.agent.ui.chat

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.components.BankIcon
import com.aibanking.agent.ui.components.BankIconKind
import com.aibanking.agent.ui.components.BankSemanticStatus
import com.aibanking.agent.ui.components.BankStatusChip
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.platform.testTag

/** Reply facts use a quiet inset panel instead of another full page card. */
@Composable
internal fun ReplyPanel(
    modifier: Modifier = Modifier,
    color: Color = MaterialTheme.colorScheme.surfaceContainer,
    contentColor: Color = MaterialTheme.colorScheme.onSurface,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(modifier = modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp),
        color = color, contentColor = contentColor) {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)
    }
}

@Composable
internal fun ReplyIconTile(icon: BankIconKind, modifier: Modifier = Modifier, accent: Color = MaterialTheme.colorScheme.primaryContainer) {
    Surface(modifier, shape = CircleShape, color = accent) {
        Box(Modifier.size(32.dp), contentAlignment = Alignment.Center) {
            BankIcon(icon, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onPrimaryContainer)
        }
    }
}

@Composable
internal fun ReplyDivider(modifier: Modifier = Modifier, color: Color = MaterialTheme.colorScheme.outlineVariant) {
    Canvas(modifier.fillMaxWidth().height(1.dp)) {
        drawLine(color, Offset(0f, size.height / 2), Offset(size.width, size.height / 2),
            strokeWidth = 1.dp.toPx())
    }
}

@Composable
internal fun ReplyValueHero(label: String, value: String) {
    Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.primaryContainer) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onPrimaryContainer)
            val style = MaterialTheme.typography.headlineMedium.copy(fontFeatureSettings = "tnum", fontWeight = FontWeight.Medium)
            // Financial facts are visible at their exact value from the first frame.
            Text(value, style = style, color = MaterialTheme.colorScheme.onPrimaryContainer)
        }
    }
}

/** Only explicit server statuses determine outcome color, never the reply prose or risk tier. */
@Composable
internal fun ReplyStatusChip(status: String, label: String, modifier: Modifier = Modifier) {
    val semantic = when (status.uppercase()) {
        "SUCCEEDED", "SUCCESS", "COMPLETED", "SETTLED", "COLLECTED", "CARD_PROTECTED",
        "ISSUED", "LIMIT_UPDATED", "PURCHASED", "REDEEMED", "LOCKED", "PASSWORD_CHANGED" -> BankSemanticStatus.RiskGreen
        "FAILED", "FAILURE", "ERROR", "BLOCKED", "DENIED", "REJECTED" -> BankSemanticStatus.RiskRed
        "PENDING", "WAITING_USER", "PENDING_REVIEW", "PENDING_COLLECTION", "PARTIALLY_COLLECTED", "PARTIAL",
        "NEEDS_INPUT", "WAITING_APPROVAL", "PENDING_RECONCILIATION", "PARTIAL_SUCCESS" -> BankSemanticStatus.RiskYellow
        else -> BankSemanticStatus.Neutral
    }
    BankStatusChip(label, semantic, modifier)
}

@Composable
internal fun WorkflowProgress(stages: List<com.aibanking.agent.data.StageProgress>, showTimings: Boolean = false) {
    val colors = MaterialTheme.colorScheme
    val semantic = LocalBankSemanticColors.current
    Column(Modifier.fillMaxWidth().testTag("workflow_progress")) {
        stages.forEachIndexed { index, stage ->
            val (label, icon, tint) = when (stage.phase) {
                "completed" -> Triple("已完成", BankIconKind.Check, semantic.riskGreen)
                "failed" -> Triple("未完成", BankIconKind.Close, semantic.riskRed)
                "waiting" -> Triple("待您确认", BankIconKind.Shield, semantic.riskYellow)
                "started" -> Triple("进行中", BankIconKind.More, colors.primary)
                else -> Triple("状态待更新", BankIconKind.More, colors.onSurfaceVariant)
            }
            val last = index == stages.lastIndex
            Box(Modifier.fillMaxWidth()) {
                if (!last) Canvas(Modifier.matchParentSize()) {
                    drawLine(colors.outlineVariant, Offset(14.dp.toPx(), 28.dp.toPx()),
                        Offset(14.dp.toPx(), size.height), strokeWidth = 1.dp.toPx())
                }
                Row(Modifier.fillMaxWidth().padding(bottom = if (last) 0.dp else 14.dp)
                    .semantics(mergeDescendants = true) {}, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Surface(shape = CircleShape, color = tint.copy(alpha = 0.1f)) {
                        Box(Modifier.size(28.dp), contentAlignment = Alignment.Center) {
                            if (stage.phase == "started" && LocalBankMotionEnabled.current) {
                                CircularProgressIndicator(Modifier.size(16.dp), color = tint, strokeWidth = 1.5.dp)
                            } else BankIcon(icon, Modifier.size(16.dp), tint = tint)
                        }
                    }
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        Text(stage.label.ifBlank { "处理步骤" }, style = MaterialTheme.typography.bodyMedium,
                            color = colors.onSurface, fontWeight = FontWeight.Medium)
                        val timing = if (showTimings && stage.phase == "completed") " · ${stage.elapsedMs} ms" else ""
                        Text(label + timing, style = MaterialTheme.typography.labelSmall, color = tint)
                    }
                }
            }
        }
    }
}
