package com.aibanking.agent.ui.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.components.BankIcon
import com.aibanking.agent.ui.components.BankIconKind

/** A readable reply with financial facts kept separate from support metadata. */
@Composable
internal fun AgentReplyCard(message: ChatMessage, modifier: Modifier = Modifier) {
    var showDetails by rememberSaveable(message.id) { mutableStateOf(false) }
    val colors = MaterialTheme.colorScheme
    Surface(
        modifier = modifier.fillMaxWidth().padding(vertical = 4.dp).testTag("reply_card_${message.id}"),
        shape = MaterialTheme.shapes.large,
        color = colors.surface,
        border = BorderStroke(1.dp, colors.outlineVariant.copy(alpha = 0.55f)),
    ) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Surface(shape = MaterialTheme.shapes.small, color = colors.primaryContainer, contentColor = colors.onPrimaryContainer) {
                    Box(Modifier.size(34.dp), contentAlignment = Alignment.Center) {
                        BankIcon(BankIconKind.Chat, Modifier.size(19.dp))
                    }
                }
                Text("安言", style = MaterialTheme.typography.titleSmall, color = colors.onSurface)
                Text("AI 助手", style = MaterialTheme.typography.labelMedium, color = colors.onSurfaceVariant)
            }
            SelectionContainer { AgentMarkdown(message.text) }
            ResultExtras(message.result)
            message.traceId?.takeIf { it.isNotBlank() }?.let { traceId ->
                Column {
                    HorizontalDivider(color = colors.outlineVariant.copy(alpha = 0.55f))
                    TextButton(
                        onClick = { showDetails = !showDetails },
                        modifier = Modifier.testTag("reply_details_${message.id}"),
                        contentPadding = PaddingValues(horizontal = 0.dp, vertical = 8.dp),
                        colors = ButtonDefaults.textButtonColors(contentColor = colors.onSurfaceVariant),
                    ) {
                        Text(if (showDetails) "收起详情" else "回复详情", style = MaterialTheme.typography.labelMedium)
                        Spacer(Modifier.width(6.dp))
                        BankIcon(if (showDetails) BankIconKind.Close else BankIconKind.More, Modifier.size(16.dp))
                    }
                    AnimatedVisibility(
                        visible = showDetails,
                        enter = fadeIn() + expandVertically(),
                        exit = fadeOut() + shrinkVertically(),
                    ) {
                        Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
                            Text("回复编号", style = MaterialTheme.typography.labelMedium, color = colors.onSurfaceVariant)
                            SelectionContainer {
                                Text(traceId, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace,
                                    fontWeight = FontWeight.Medium, color = colors.onSurfaceVariant)
                            }
                        }
                    }
                }
            }
        }
    }
}
