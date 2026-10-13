package com.aibanking.agent.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aibanking.agent.ui.theme.BankPalette
import java.net.URI

/** Render model formatting as native text; never strip meaningful '*' characters. */
@Composable
fun AgentMarkdown(text: String, modifier: Modifier = Modifier) {
    val blocks = remember(text) { parseReplyMarkdown(text) }
    ReplyBlocks(blocks, modifier)
}

@Composable
private fun ReplyBlocks(blocks: List<ReplyBlock>, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        blocks.forEach { block ->
            when (block) {
                is ReplyBlock.Paragraph -> {
                    val title = block.inlines.any { it.text.isNotBlank() } && block.inlines.all { it.bold || it.text.isBlank() }
                    if (title) ReplyHeading(block.inlines)
                    else Text(replyText(block.inlines), style = MaterialTheme.typography.bodyMedium.copy(lineHeight = 24.sp))
                }
                is ReplyBlock.Heading -> ReplyHeading(block.inlines)
                is ReplyBlock.ListBlock -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    block.items.forEachIndexed { index, item ->
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            if (block.ordered) {
                                Surface(shape = RoundedCornerShape(6.dp), color = MaterialTheme.colorScheme.primaryContainer,
                                    border = BorderStroke(BankPalette.borderWidth, MaterialTheme.colorScheme.outline)) {
                                    Text("${block.startNumber + index}.", Modifier.widthIn(min = 24.dp).padding(horizontal = 5.dp, vertical = 2.dp),
                                        style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onPrimaryContainer,
                                        fontWeight = FontWeight.Bold)
                                }
                            } else {
                                Box(Modifier.width(14.dp).height(24.dp), contentAlignment = Alignment.Center) {
                                    Box(Modifier.size(6.dp).rotate(45f).background(MaterialTheme.colorScheme.primary))
                                }
                            }
                            ReplyBlocks(item, Modifier.weight(1f))
                        }
                    }
                }
                is ReplyBlock.Quote -> Surface(shape = MaterialTheme.shapes.small, color = MaterialTheme.colorScheme.primaryContainer,
                    border = BorderStroke(BankPalette.borderWidth, MaterialTheme.colorScheme.outline)) {
                    Row(Modifier.height(IntrinsicSize.Min).padding(12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Box(Modifier.width(3.dp).fillMaxHeight().background(MaterialTheme.colorScheme.outline, RoundedCornerShape(2.dp)))
                        ReplyBlocks(block.blocks, Modifier.weight(1f))
                    }
                }
                is ReplyBlock.CodeBlock -> Surface(shape = MaterialTheme.shapes.small, color = BankPalette.Ink,
                    border = BorderStroke(BankPalette.borderWidth, MaterialTheme.colorScheme.outline)) {
                    Text(
                        block.code,
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall.copy(lineHeight = 20.sp),
                        color = Color.White,
                        modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(14.dp),
                    )
                }
                ReplyBlock.Divider -> ReplyDivider(Modifier.padding(vertical = 4.dp))
            }
        }
    }
}

@Composable
private fun ReplyHeading(inlines: List<ReplyInline>) {
    Row(Modifier.fillMaxWidth().padding(top = 2.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Box(Modifier.width(4.dp).height(20.dp).background(BankPalette.Wisteria, RoundedCornerShape(2.dp)))
        Text(replyText(inlines), Modifier.weight(1f), style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurface, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun replyText(inlines: List<ReplyInline>): AnnotatedString {
    val colors = MaterialTheme.colorScheme
    return remember(inlines, colors) {
        AnnotatedString.Builder().apply {
            inlines.forEach { inline ->
                val start = length
                append(inline.text)
                addStyle(SpanStyle(
                    fontWeight = if (inline.bold) FontWeight.Bold else null,
                    fontStyle = if (inline.italic) FontStyle.Italic else null,
                    fontFamily = if (inline.code) FontFamily.Monospace else null,
                    background = if (inline.code) colors.primaryContainer else Color.Unspecified,
                ), start, length)
                // Unsupported schemes remain legible text, never executable intents.
                inline.link?.takeIf { target ->
                    runCatching { URI(target).let { it.scheme in setOf("https", "http") && !it.host.isNullOrBlank() } }.getOrDefault(false)
                }?.let { target ->
                    addLink(LinkAnnotation.Url(target, TextLinkStyles(SpanStyle(color = colors.secondary, textDecoration = TextDecoration.Underline))), start, length)
                }
            }
        }.toAnnotatedString()
    }
}
