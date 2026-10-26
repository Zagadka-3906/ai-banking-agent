package com.aibanking.agent.ui.chat

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
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
import com.aibanking.agent.ui.anim.riseIn
import java.net.URI

/** Render model formatting as native text; never strip meaningful '*' characters. */
@Composable
fun AgentMarkdown(
    text: String,
    modifier: Modifier = Modifier,
    animateReveal: Boolean = false,
) {
    val blocks = remember(text) { parseReplyMarkdown(text) }
    ReplyBlocks(blocks, modifier, animateReveal = animateReveal)
}

@Composable
private fun ReplyBlocks(
    blocks: List<ReplyBlock>,
    modifier: Modifier = Modifier,
    animateReveal: Boolean = false,
) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        blocks.forEachIndexed { blockIndex, block ->
            val revealMod = if (animateReveal) {
                Modifier.riseIn(
                    animate = true,
                    delayMillis = (blockIndex * 55).coerceAtMost(330),
                    durationMillis = 260,
                    rise = 4.dp,
                )
            } else {
                Modifier
            }
            Box(revealMod.fillMaxWidth()) {
                when (block) {
                    is ReplyBlock.Paragraph -> {
                        val title = block.inlines.any { it.text.isNotBlank() } && block.inlines.all { it.bold || it.text.isBlank() }
                        if (title) ReplyHeading(block.inlines)
                        else Text(replyText(block.inlines), style = MaterialTheme.typography.bodyLarge.copy(lineHeight = 26.sp))
                    }
                    is ReplyBlock.Heading -> ReplyHeading(block.inlines)
                    is ReplyBlock.ListBlock -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        block.items.forEachIndexed { index, item ->
                            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                if (block.ordered) {
                                    Text(
                                        "${block.startNumber + index}.",
                                        Modifier.widthIn(min = 20.dp),
                                        style = MaterialTheme.typography.bodyLarge,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                } else {
                                    Box(Modifier.width(14.dp).height(26.dp), contentAlignment = Alignment.Center) {
                                        Box(Modifier.size(5.dp).background(MaterialTheme.colorScheme.onSurface, CircleShape))
                                    }
                                }
                                ReplyBlocks(item, Modifier.weight(1f), animateReveal = false)
                            }
                        }
                    }
                    is ReplyBlock.Quote -> Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainer) {
                        Row(Modifier.height(IntrinsicSize.Min).padding(12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Box(Modifier.width(3.dp).fillMaxHeight().background(MaterialTheme.colorScheme.outline, RoundedCornerShape(2.dp)))
                            ReplyBlocks(block.blocks, Modifier.weight(1f), animateReveal = false)
                        }
                    }
                    is ReplyBlock.CodeBlock -> Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainerHigh) {
                        Text(
                            block.code,
                            fontFamily = FontFamily.Monospace,
                            style = MaterialTheme.typography.bodySmall.copy(lineHeight = 20.sp),
                            color = MaterialTheme.colorScheme.onSurface,
                            modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(14.dp),
                        )
                    }
                    ReplyBlock.Divider -> ReplyDivider(Modifier.padding(vertical = 4.dp))
                }
            }
        }
    }
}

@Composable
private fun ReplyHeading(inlines: List<ReplyInline>) {
    Row(
        Modifier.fillMaxWidth().padding(top = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            replyText(inlines),
            Modifier.weight(1f),
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurface,
            fontWeight = FontWeight.SemiBold,
        )
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
                addStyle(
                    SpanStyle(
                        fontWeight = if (inline.bold) FontWeight.Bold else null,
                        fontStyle = if (inline.italic) FontStyle.Italic else null,
                        fontFamily = if (inline.code) FontFamily.Monospace else null,
                        background = if (inline.code) colors.primaryContainer else Color.Unspecified,
                    ),
                    start,
                    length,
                )
                // Unsupported schemes remain legible text, never executable intents.
                inline.link?.takeIf { target ->
                    runCatching { URI(target).let { it.scheme in setOf("https", "http") && !it.host.isNullOrBlank() } }.getOrDefault(false)
                }?.let { target ->
                    addLink(
                        LinkAnnotation.Url(
                            target,
                            TextLinkStyles(SpanStyle(color = colors.secondary, textDecoration = TextDecoration.Underline)),
                        ),
                        start,
                        length,
                    )
                }
            }
        }.toAnnotatedString()
    }
}
