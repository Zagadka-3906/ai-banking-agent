package com.aibanking.agent

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.chat.*
import com.aibanking.agent.ui.theme.BankAgentTheme
import java.io.File
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

@BankingRegression
class InteractionPolishUiTest {
    @get:Rule val ui = createAndroidComposeRule<ComponentActivity>()

    @Test fun history_filters_select_one_segment_and_keep_both_filters_independent() {
        filters(false)
        ui.onNodeWithTag("history_date_segments_MONTH").performClick().assertIsSelected()
        ui.onNodeWithTag("history_category_segments_理财申购").performScrollTo().performClick().assertIsSelected()
        ui.onNodeWithTag("history_date_segments_MONTH").assertIsSelected()
        ui.onNodeWithTag("history_category_segments_ALL").assertIsNotSelected()
        screenshot("history-segmented-controls")
    }

    @Test fun moving_selection_stretches_ten_percent_then_returns_to_its_width() {
        ui.mainClock.autoAdvance = false
        filters(true)
        ui.mainClock.advanceTimeByFrame()
        ui.mainClock.advanceTimeBy(400)
        val initial = selectedWidth()
        ui.onNodeWithTag("history_date_segments_TODAY").performClick()
        ui.mainClock.advanceTimeByFrame()
        ui.mainClock.advanceTimeBy(160)
        val midpoint = selectedWidth()
        assertEquals("mid-flight indicator should stretch about ten percent", initial * 1.10, midpoint.toDouble(), 4.0)
        screenshot("history-segment-stretch")
        ui.mainClock.advanceTimeBy(400)
        val targetWidth = ui.onNodeWithTag("history_date_segments_TODAY").fetchSemanticsNode().boundsInRoot.width
        assertEquals(targetWidth.toDouble(), selectedWidth().toDouble(), 2.0)
    }

    @Test fun thinking_animates_the_orb_while_the_anyan_avatar_stays_fixed() {
        ui.mainClock.autoAdvance = false
        ui.setContent { BankAgentTheme { CompositionLocalProvider(LocalBankMotionEnabled provides true) {
            Surface { ThinkingReply(emptyList(), Modifier.padding(20.dp)) }
        } } }
        ui.mainClock.advanceTimeBy(80)
        val avatar = ui.onNodeWithTag("thinking_avatar").captureToImage().asAndroidBitmap()
        val orb = ui.onNodeWithTag("thinking_orbs").captureToImage().asAndroidBitmap()
        ui.mainClock.advanceTimeBy(600)
        assertTrue(avatar.sameAs(ui.onNodeWithTag("thinking_avatar").captureToImage().asAndroidBitmap()))
        assertFalse(orb.sameAs(ui.onNodeWithTag("thinking_orbs").captureToImage().asAndroidBitmap()))
        screenshot("thinking-orbs-static-avatar")
    }

    private fun filters(motion: Boolean) {
        ui.setContent { BankAgentTheme { CompositionLocalProvider(LocalBankMotionEnabled provides motion) {
            var date by remember { mutableStateOf("ALL") }
            var category by remember { mutableStateOf<String?>(null) }
            Surface { Column(Modifier.width(360.dp).padding(vertical = 20.dp)) {
                DateScopeFilterRow(date) { date = it }
                CategoryFilterRow(category) { category = it }
            } }
        } } }
    }

    private fun selectedWidth(): Int {
        val pixels = ui.onNodeWithTag("history_date_segments").captureToImage().toPixelMap()
        val white = (0 until pixels.width).filter { x ->
            val color = pixels[x, pixels.height / 2]
            color.red > .99f && color.green > .99f && color.blue > .99f && color.alpha > .99f
        }
        assertTrue("selection must be visible", white.isNotEmpty())
        return white.last() - white.first() + 1
    }

    private fun screenshot(name: String) {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val folder = File(context.getExternalFilesDir(null), "interaction-review").also { it.mkdirs() }
        File(folder, "$name.png").outputStream().use {
            ui.onRoot().captureToImage().asAndroidBitmap().compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
        }
    }
}
