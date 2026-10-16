package com.aibanking.agent.ui.chat

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.components.BankIcon
import com.aibanking.agent.ui.components.BankIconKind
import com.aibanking.agent.ui.components.BankSemanticStatus
import com.aibanking.agent.ui.components.BankStatusChip
import com.aibanking.agent.ui.theme.BankPalette
import com.aibanking.agent.ui.anim.CountUpText

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
            val amount = MONEY_TEXT.matchEntire(value)?.groupValues?.get(1)?.replace(",", "")?.toDoubleOrNull()
            val style = MaterialTheme.typography.headlineMedium.copy(fontFeatureSettings = "tnum", fontWeight = FontWeight.Medium)
            if (amount != null) {
                // Money counts up once; the semantics keep the exact amount.
                CountUpText(amount, { "¥" + String.format(java.util.Locale.CHINA, "%,.2f", it) },
                    style = style, color = MaterialTheme.colorScheme.onPrimaryContainer)
            } else {
                Text(value, style = style, color = MaterialTheme.colorScheme.onPrimaryContainer)
            }
        }
    }
}

private val MONEY_TEXT = Regex("""¥(\d{1,3}(?:,\d{3})*\.\d{2})""")

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
internal fun WorkflowProgress(stages: List<com.aibanking.agent.data.StageProgress>) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        stages.forEach { stage ->
            val suffix = when (stage.phase) {
                "completed" -> "完成 · ${stage.elapsedMs} ms"
                "failed" -> "未完成"
                "waiting" -> "待您确认"
                else -> "进行中"
            }
            Text("${stage.label} · $suffix", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
