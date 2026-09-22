package com.aibanking.agent.ui.chat

import org.commonmark.node.BlockQuote
import org.commonmark.node.BulletList
import org.commonmark.node.Code
import org.commonmark.node.Emphasis
import org.commonmark.node.FencedCodeBlock
import org.commonmark.node.HardLineBreak
import org.commonmark.node.Heading
import org.commonmark.node.HtmlBlock
import org.commonmark.node.HtmlInline
import org.commonmark.node.Image
import org.commonmark.node.IndentedCodeBlock
import org.commonmark.node.Link
import org.commonmark.node.LinkReferenceDefinition
import org.commonmark.node.ListItem
import org.commonmark.node.Node
import org.commonmark.node.OrderedList
import org.commonmark.node.Paragraph
import org.commonmark.node.SoftLineBreak
import org.commonmark.node.StrongEmphasis
import org.commonmark.node.Text
import org.commonmark.node.ThematicBreak
import org.commonmark.parser.Parser
import org.commonmark.parser.beta.InlineContentParser
import org.commonmark.parser.beta.InlineContentParserFactory
import org.commonmark.parser.beta.ParsedInline

/** UI-independent reply blocks. List items may contain paragraphs and nested blocks. */
sealed interface ReplyBlock {
    data class Paragraph(val inlines: List<ReplyInline>) : ReplyBlock
    data class Heading(val level: Int, val inlines: List<ReplyInline>) : ReplyBlock
    data class ListBlock(
        val ordered: Boolean,
        val startNumber: Int,
        val items: List<List<ReplyBlock>>,
    ) : ReplyBlock
    data class Quote(val blocks: List<ReplyBlock>) : ReplyBlock
    data class CodeBlock(val code: String, val language: String? = null) : ReplyBlock
    data object Divider : ReplyBlock
}

/** Flat runs let Compose render nested emphasis with one AnnotatedString per paragraph. */
data class ReplyInline(
    val text: String,
    val bold: Boolean = false,
    val italic: Boolean = false,
    val code: Boolean = false,
    val link: String? = null,
)

// commonmark 0.30.0 is thread-safe, bounds nesting, and has no Android/UI dependency.
// https://github.com/commonmark/commonmark-java/tree/commonmark-parent-0.30.0
private val replyMarkdownParser: Parser = Parser.builder()
    .customInlineContentParserFactory(NumericMaskParserFactory)
    .build()

/**
 * Banking replies contain masks such as 138****1234. CommonMark otherwise pairs the
 * stars of separate phone numbers as nested emphasis and silently removes the masks.
 * Intercept only four-or-more stars beside a digit before they enter the delimiter
 * stack. Ordinary **100** emphasis still uses CommonMark; code, backslash escapes and
 * link destinations are consumed by their own parsers and never rewritten here.
 * This small adapter uses CommonMark's published beta inline extension API, pinned
 * with the dependency version; it does not depend on its internal implementation.
 */
private object NumericMaskParserFactory : InlineContentParserFactory {
    override fun getTriggerCharacters(): Set<Char> = setOf('*')

    override fun create(): InlineContentParser = InlineContentParser { state ->
        val scanner = state.scanner()
        val start = scanner.position()
        val before = scanner.peekPreviousCodePoint()
        val count = scanner.matchMultiple('*')
        if (count >= 4 && (Character.isDigit(before) || Character.isDigit(scanner.peekCodePoint()))) {
            ParsedInline.of(Text("*".repeat(count)), scanner.position())
        } else {
            scanner.setPosition(start)
            ParsedInline.none()
        }
    }
}

/**
 * Parse completed assistant replies using CommonMark grammar, never global star replacement.
 * Card masks, arithmetic, escaped delimiters and code retain their literal content. An
 * unmatched delimiter remains text; guessing that it is formatting could destroy account data.
 * HTML remains visible text and images become their alt text/link; nothing is executed or fetched.
 * Link destinations are data only: the renderer decides which schemes it can open.
 */
fun parseReplyMarkdown(source: String): List<ReplyBlock> =
    replyMarkdownParser.parse(source).replyBlocks()

private fun Node.replyBlocks(): List<ReplyBlock> = buildList {
    var child = firstChild
    while (child != null) {
        when (val node = child) {
            is Paragraph -> add(ReplyBlock.Paragraph(node.replyInlines()))
            is Heading -> add(ReplyBlock.Heading(node.level, node.replyInlines()))
            is BlockQuote -> add(ReplyBlock.Quote(node.replyBlocks()))
            is BulletList -> add(ReplyBlock.ListBlock(false, 1, node.replyListItems()))
            is OrderedList -> add(ReplyBlock.ListBlock(true, node.markerStartNumber ?: 1, node.replyListItems()))
            is FencedCodeBlock -> add(
                ReplyBlock.CodeBlock(
                    code = node.literal,
                    language = node.info?.trim()?.takeWhile { !it.isWhitespace() }?.takeIf { it.isNotEmpty() },
                ),
            )
            is IndentedCodeBlock -> add(ReplyBlock.CodeBlock(node.literal))
            is ThematicBreak -> add(ReplyBlock.Divider)
            is HtmlBlock -> add(ReplyBlock.Paragraph(listOf(ReplyInline(node.literal))))
            is LinkReferenceDefinition -> Unit // Resolved references live on Link nodes.
            else -> addAll(node.replyBlocks())
        }
        child = child.next
    }
}

private fun Node.replyListItems(): List<List<ReplyBlock>> = buildList {
    var child = firstChild
    while (child != null) {
        if (child is ListItem) add(child.replyBlocks())
        child = child.next
    }
}

private fun Node.replyInlines(): List<ReplyInline> = buildList {
    appendReplyInlines(this@replyInlines, ReplyInline(""))
}

private fun MutableList<ReplyInline>.appendReplyInlines(parent: Node, style: ReplyInline) {
    var child = parent.firstChild
    while (child != null) {
        when (val node = child) {
            is Text -> appendRun(style.copy(text = node.literal))
            is Code -> appendRun(style.copy(text = node.literal, code = true))
            is StrongEmphasis -> appendReplyInlines(node, style.copy(bold = true))
            is Emphasis -> appendReplyInlines(node, style.copy(italic = true))
            is Link -> appendReplyInlines(node, style.copy(link = node.destination))
            is Image -> {
                // Retain meaningful alt text without loading a network image inside a bank reply.
                if (node.firstChild == null) appendRun(style.copy(text = node.destination, link = node.destination))
                else appendReplyInlines(node, style.copy(link = node.destination))
            }
            is SoftLineBreak, is HardLineBreak -> appendRun(style.copy(text = "\n"))
            is HtmlInline -> appendRun(style.copy(text = node.literal))
            else -> appendReplyInlines(node, style)
        }
        child = child.next
    }
}

private fun MutableList<ReplyInline>.appendRun(run: ReplyInline) {
    if (run.text.isEmpty()) return
    val previous = lastOrNull()
    if (previous != null && previous.copy(text = run.text) == run) {
        this[lastIndex] = previous.copy(text = previous.text + run.text)
    } else {
        add(run)
    }
}
