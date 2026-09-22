package com.aibanking.agent

import com.aibanking.agent.ui.chat.ReplyBlock
import com.aibanking.agent.ui.chat.ReplyInline
import com.aibanking.agent.ui.chat.parseReplyMarkdown
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ReplyMarkdownParserTest {
    @Test fun rendersChineseEmphasisAndFinancialAmountsWithoutDelimiterCharacters() {
        val runs = paragraph("当前**可用余额**为 **¥12,345.67**，请*核对*。")
        assertEquals("当前可用余额为 ¥12,345.67，请核对。", runs.joinToString("") { it.text })
        assertEquals(listOf("可用余额", "¥12,345.67"), runs.filter { it.bold }.map { it.text })
        assertEquals(listOf("核对"), runs.filter { it.italic }.map { it.text })
    }

    @Test fun combinesNestedBoldAndItalicWithoutLosingSurroundingText() {
        assertEquals(
            listOf(ReplyInline("请 "), ReplyInline("认真核对", bold = true, italic = true), ReplyInline(" 收款人")),
            paragraph("请 ***认真核对*** 收款人"),
        )
    }

    @Test fun preservesMaskedCardNumbersAndArithmeticStars() {
        val source = "卡号 **** 1234，另一张 6222 **** **** 5678；计算 2 * 3 = 6。"
        assertEquals(listOf(ReplyInline(source)), paragraph(source))
        assertEquals(listOf(ReplyInline("**** 1234")), paragraph("**** 1234"))
    }

    @Test fun preservesTwoUnspacedMaskedPhoneNumbers() {
        val source = "138****1234 and 139****5678"
        assertEquals(listOf(ReplyInline(source)), paragraph(source))
    }

    @Test fun preservesMultipleMaskedSegmentsInOneBankCardNumber() {
        val source = "6222****1234****5678"
        assertEquals(listOf(ReplyInline(source)), paragraph(source))
    }

    @Test fun keepsNumericMasksAlongsideAndInsideOrdinaryBoldEmphasis() {
        val runs = paragraph("**收款人** 138****1234，备用 139****5678；银行卡 **6222****1234****5678**；金额 **100** 元。")
        assertEquals(
            "收款人 138****1234，备用 139****5678；银行卡 6222****1234****5678；金额 100 元。",
            runs.joinToString("") { it.text },
        )
        assertEquals(listOf("收款人", "6222****1234****5678", "100"), runs.filter { it.bold }.map { it.text })
        assertTrue(runs.none { it.italic })
    }

    @Test fun numericMaskProtectionLeavesCodeAndEscapedStarsUnchanged() {
        val runs = paragraph("`138****1234 and 139****5678`；138\\*\\*\\*\\*1234；**100**")
        assertEquals("138****1234 and 139****5678", runs.single { it.code }.text)
        assertEquals("138****1234 and 139****5678；138****1234；100", runs.joinToString("") { it.text })
        assertEquals("100", runs.single { it.bold }.text)
        assertEquals(
            listOf(ReplyBlock.CodeBlock("138****1234 and 139****5678\n")),
            parseReplyMarkdown("```\n138****1234 and 139****5678\n```"),
        )
    }

    @Test fun numericMaskProtectionPreservesLinkLabelsAndRawDestinations() {
        val runs = paragraph("[138****1234](https://example.com/138****1234) 和 <https://example.com/139****5678>")
        assertEquals("138****1234", runs.first().text)
        assertEquals("https://example.com/138****1234", runs.first().link)
        assertEquals("https://example.com/139****5678", runs.last().text)
        assertEquals("https://example.com/139****5678", runs.last().link)
    }

    @Test fun preservesEscapedStarsAsVisibleText() {
        assertEquals(listOf(ReplyInline("示例 **文本** 和 * 星号")), paragraph("示例 \\*\\*文本\\*\\* 和 \\* 星号"))
    }

    @Test fun preservesLiteralMarkdownAndMaskedAccountsInsideInlineCode() {
        val runs = paragraph("字段 `**金额**` 与 `CARD-****1234`，计算 `2 * 3`。")
        assertEquals(listOf("**金额**", "CARD-****1234", "2 * 3"), runs.filter { it.code }.map { it.text })
        assertTrue(runs.none { it.bold || it.italic })
    }

    @Test fun keepsFencedCodeAndLanguageWithoutParsingItsAsterisks() {
        val code = "val mask = \"**** 1234\"\nval amount = 2 * 3\n"
        assertEquals(
            listOf(ReplyBlock.CodeBlock(code, "kotlin")),
            parseReplyMarkdown("```kotlin title=example\n${code}```"),
        )
    }

    @Test fun supportsIndentedCodeBlocks() {
        assertEquals(listOf(ReplyBlock.CodeBlock("**not bold**\n")), parseReplyMarkdown("    **not bold**\n"))
    }

    @Test fun preservesLinkDestinationAndEmphasizedLabel() {
        val runs = paragraph("查看 [**对账单**](https://example.com/report?mask=**** \"说明\")。")
        assertEquals("查看 对账单。", runs.joinToString("") { it.text })
        val linked = runs.single { it.link != null }
        assertEquals("对账单", linked.text)
        assertTrue(linked.bold)
        assertEquals("https://example.com/report?mask=****", linked.link)
    }

    @Test fun resolvesReferenceLinksWithoutDisplayingTheirDefinitions() {
        val blocks = parseReplyMarkdown("[帮助][guide]\n\n[guide]: https://example.com/help \"帮助中心\"")
        assertEquals(listOf(ReplyBlock.Paragraph(listOf(ReplyInline("帮助", link = "https://example.com/help")))), blocks)
    }

    @Test fun preservesAutolinksAndLeavesPlainUrlTextIntact() {
        val runs = paragraph("<https://example.com/a> https://example.com/b?mask=****")
        assertEquals("https://example.com/a", runs.first().link)
        assertEquals(" https://example.com/b?mask=****", runs.last().text)
        assertNull(runs.last().link)
    }

    @Test fun supportsNestedListsAndOriginalOrderedListStartingNumber() {
        val blocks = parseReplyMarkdown("3. 核对**收款人**\n   - 姓名\n   - 卡号 **** 1234\n4. 确认金额")
        val list = blocks.single() as ReplyBlock.ListBlock
        assertTrue(list.ordered)
        assertEquals(3, list.startNumber)
        assertEquals(2, list.items.size)
        val first = list.items.first()
        assertEquals("核对收款人", (first[0] as ReplyBlock.Paragraph).inlines.joinToString("") { it.text })
        val nested = first[1] as ReplyBlock.ListBlock
        assertFalse(nested.ordered)
        assertEquals(2, nested.items.size)
        assertEquals(listOf(ReplyBlock.Paragraph(listOf(ReplyInline("卡号 **** 1234")))), nested.items[1])
    }

    @Test fun supportsHeadingsQuotesAndDividersAsSeparateBlocks() {
        val blocks = parseReplyMarkdown("## 处理**进展**\n\n> 尚未扣款\n>\n> 请核对金额\n\n---")
        val heading = blocks[0] as ReplyBlock.Heading
        assertEquals(2, heading.level)
        assertEquals(listOf(ReplyInline("处理"), ReplyInline("进展", bold = true)), heading.inlines)
        val quote = blocks[1] as ReplyBlock.Quote
        assertEquals(2, quote.blocks.size)
        assertEquals(ReplyBlock.Divider, blocks[2])
    }

    @Test fun keepsSoftAndHardLineBreaksReadable() {
        val runs = paragraph("第一行\n第二行  \n第三行")
        assertEquals("第一行\n第二行\n第三行", runs.joinToString("") { it.text })
    }

    @Test fun treatsHtmlAsLiteralTextRatherThanDiscardingOrExecutingIt() {
        assertEquals(listOf(ReplyInline("金额 <b>100</b> 元")), paragraph("金额 <b>100</b> 元"))
        assertEquals(
            listOf(ReplyBlock.Paragraph(listOf(ReplyInline("<script>alert('x')</script>")))),
            parseReplyMarkdown("<script>alert('x')</script>"),
        )
    }

    @Test fun keepsUnmatchedDelimitersAndMalformedLinksInsteadOfGuessing() {
        listOf("正在处理 **金额", "卡号 ****", "查看 [详情](未完成", "末尾 **", "独立 * 星号").forEach { source ->
            assertEquals(source, paragraph(source).joinToString("") { it.text })
        }
    }

    @Test fun rendersImagesAsAltTextWithDestinationWithoutFetchingThem() {
        assertEquals(
            listOf(ReplyInline("凭证 **** 1234", link = "https://example.com/proof.png")),
            paragraph("![凭证 **** 1234](https://example.com/proof.png)"),
        )
    }

    @Test fun emptyRepliesDoNotCreateEmptyCards() {
        assertTrue(parseReplyMarkdown("").isEmpty())
        assertTrue(parseReplyMarkdown(" \n\t\n").isEmpty())
    }

    private fun paragraph(source: String): List<ReplyInline> =
        (parseReplyMarkdown(source).single() as ReplyBlock.Paragraph).inlines
}
