package com.aibanking.agent.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
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
import java.net.URI

/** Render model formatting as native text; never strip meaningful '*' characters. */
@Composable
fun AgentMarkdown(text: String, modifier: Modifier = Modifier) {
    val blocks = remember(text) { parseReplyMarkdown(text) }
    ReplyBlocks(blocks, modifier)
}

@Composable
private fun ReplyBlocks(blocks: List<ReplyBlock>, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        blocks.forEach { block ->
            when (block) {
                is ReplyBlock.Paragraph -> Text(replyText(block.inlines), style = MaterialTheme.typography.bodyLarge.copy(lineHeight = 28.sp))
                is ReplyBlock.Heading -> Text(
                    replyText(block.inlines),
                    style = if (block.level <= 2) MaterialTheme.typography.titleLarge else MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.padding(top = 4.dp),
                )
                is ReplyBlock.ListBlock -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    block.items.forEachIndexed { index, item ->
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            Text(
                                if (block.ordered) "${block.startNumber + index}." else "•",
                                style = MaterialTheme.typography.bodyLarge.copy(lineHeight = 28.sp),
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.widthIn(min = 14.dp),
                            )
                            ReplyBlocks(item, Modifier.weight(1f))
                        }
                    }
                }
                is ReplyBlock.Quote -> Surface(shape = MaterialTheme.shapes.small, color = MaterialTheme.colorScheme.surfaceContainerLow) {
                    Row(Modifier.height(IntrinsicSize.Min).padding(12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Box(Modifier.width(3.dp).fillMaxHeight().background(MaterialTheme.colorScheme.primary.copy(alpha = 0.5f), RoundedCornerShape(2.dp)))
                        ReplyBlocks(block.blocks, Modifier.weight(1f))
                    }
                }
                is ReplyBlock.CodeBlock -> Surface(shape = MaterialTheme.shapes.small, color = MaterialTheme.colorScheme.surfaceContainerLow) {
                    Text(
                        block.code,
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(14.dp),
                    )
                }
                ReplyBlock.Divider -> HorizontalDivider(Modifier.padding(vertical = 4.dp), color = MaterialTheme.colorScheme.outlineVariant)
            }
        }
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
                    fontWeight = if (inline.bold) FontWeight.SemiBold else null,
                    fontStyle = if (inline.italic) FontStyle.Italic else null,
                    fontFamily = if (inline.code) FontFamily.Monospace else null,
                    background = if (inline.code) colors.surfaceContainerHighest else androidx.compose.ui.graphics.Color.Unspecified,
                ), start, length)
                // Unsupported schemes remain legible text, never executable intents.
                inline.link?.takeIf { target ->
                    runCatching { URI(target).let { it.scheme in setOf("https", "http") && !it.host.isNullOrBlank() } }.getOrDefault(false)
                }?.let { target ->
                    addLink(LinkAnnotation.Url(target, TextLinkStyles(SpanStyle(color = colors.primary, textDecoration = TextDecoration.Underline))), start, length)
                }
            }
        }.toAnnotatedString()
    }
}
