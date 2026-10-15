package com.aibanking.agent

import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.text.TextLayoutResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue

/** Checks the laid-out text itself, including every visible character and the final character. */
internal fun SemanticsNodeInteraction.assertTextFullyVisible(expectedText: String) {
    val layouts = mutableListOf<TextLayoutResult>()
    performSemanticsAction(SemanticsActions.GetTextLayoutResult) { assertTrue(it(layouts)) }
    assertTrue("Text must provide a layout: $expectedText", layouts.isNotEmpty())

    layouts.forEach { layout ->
        val context = "Text must remain fully visible: $expectedText; size=${layout.size}"
        assertEquals("$context; layout text", expectedText, layout.layoutInput.text.text)
        assertTrue("$context; no lines", layout.lineCount > 0)

        // Compose's semantics paragraph reconstruction (verified in 1.7.5 and
        // 1.12.1) can use the parent's maxWidth while retaining the text node's
        // wrap-content size. Compare actual line/character bounds, not that
        // paragraph container's width.
        for (line in 0 until layout.lineCount) {
            assertFalse("$context; line $line is ellipsized", layout.isLineEllipsized(line))
            layout.assertBoundsInside(
                Rect(layout.getLineLeft(line), layout.getLineTop(line),
                    layout.getLineRight(line), layout.getLineBottom(line)),
                "$context; line $line",
            )
        }
        assertFalse("$context; height overflow or omitted lines", layout.didOverflowHeight)
        assertEquals("$context; first character", 0, layout.getLineStart(0))
        assertEquals("$context; final logical character", expectedText.length,
            layout.getLineEnd(layout.lineCount - 1))

        expectedText.indices.filter { !expectedText[it].isWhitespace() }.forEach { offset ->
            val line = layout.getLineForOffset(offset)
            assertTrue("$context; character $offset is omitted",
                offset >= layout.getLineStart(line) && offset < layout.getLineEnd(line, visibleEnd = true))
            layout.assertBoundsInside(layout.getBoundingBox(offset), "$context; character $offset")
        }
        val lastVisibleOffset = expectedText.indexOfLast { !it.isWhitespace() }
        if (lastVisibleOffset >= 0) {
            assertEquals("$context; final visible character", lastVisibleOffset + 1,
                layout.getLineEnd(layout.getLineForOffset(lastVisibleOffset), visibleEnd = true))
        }
    }
}

private fun TextLayoutResult.assertBoundsInside(bounds: Rect, context: String) {
    assertTrue("$context; bounds=$bounds exceed $size",
        bounds.left >= 0f && bounds.top >= 0f &&
            bounds.right <= size.width.toFloat() && bounds.bottom <= size.height.toFloat() &&
            bounds.left <= bounds.right && bounds.top <= bounds.bottom)
}
