package com.aibanking.agent

import android.graphics.Bitmap
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.chat.BankChatTopBar
import com.aibanking.agent.ui.chat.ChatWelcome
import com.aibanking.agent.ui.components.bankTextFieldColors
import com.aibanking.agent.ui.theme.BankAgentTheme
import java.io.File
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Rendered focus contrast and reachable controls; local fixtures require no service. */
@RunWith(AndroidJUnit4::class)
@BankingRegression
class DesignInputsUiTest {
    @get:Rule val ui = createComposeRule()

    @Test fun focused_light_input_keeps_its_label_and_selected_text_readable() = checkFocusedInput(false)

    @Test fun focused_dark_input_keeps_its_label_and_selected_text_readable() = checkFocusedInput(true)

    @Test fun narrow_large_text_home_explains_shortcuts_and_only_sends_the_selected_request() {
        val sent = mutableListOf<String>()
        render(dark = true) {
            Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp)) {
                ChatWelcome { sent += it }
            }
        }
        val transfer = ui.onNode(hasText("转一笔钱") and hasClickAction())
        transfer.performScrollTo().assertIsDisplayed().assertTextContains("补充信息后再确认")
        assertNoTextOverflow("补充信息后再确认")
        transfer.performClick()
        listOf("查询余额", "分析账单", "查看订阅").forEach { label ->
            ui.onNode(hasText(label) and hasClickAction()).performScrollTo()
                .assertIsDisplayed().assertTextContains("直接查询")
        }
        ui.runOnIdle { assertEquals(listOf("我想转账"), sent) }
        screenshot("home-shortcuts-dark-large-font")
    }

    @Test fun compact_toolbar_keeps_its_name_and_opens_the_drawer() {
        val called = mutableListOf<String>()
        render(dark = false) {
            BankChatTopBar(principal = "user_zhang", onMenu = { called += "menu" })
        }
        ui.onNodeWithText("安言").assertIsDisplayed()
        assertNoTextOverflow("安言")
        ui.onNodeWithTag(UiTags.OPEN_DRAWER).assertIsDisplayed().assertIsEnabled().performClick()
        ui.runOnIdle { assertEquals(listOf("menu"), called) }
        screenshot("home-toolbar-large-font")
    }

    @Test fun text_visibility_assertion_rejects_an_ellipsized_label() {
        val text = "这段文字必须被截断"
        render(dark = false) {
            Column(Modifier.fillMaxWidth()) {
                Text(text, modifier = Modifier.width(32.dp), maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        ui.onNodeWithText(text, useUnmergedTree = true).assertIsDisplayed()
        val failure = assertThrows(AssertionError::class.java) { assertNoTextOverflow(text) }
        assertTrue("The negative control must fail for ellipsis: ${failure.message}",
            failure.message.orEmpty().contains("ellipsized"))
    }

    @OptIn(ExperimentalTestApi::class)
    private fun checkFocusedInput(dark: Boolean) {
        val value = mutableStateOf(TextFieldValue("李四"))
        var scale = 1f
        render(dark) {
            scale = LocalDensity.current.density
            Column(Modifier.fillMaxWidth().padding(20.dp)) {
                OutlinedTextField(
                    value = value.value, onValueChange = { value.value = it },
                    label = { Text("收款人") }, colors = bankTextFieldColors(),
                    singleLine = true, modifier = Modifier.fillMaxWidth().testTag("design_input"),
                )
            }
        }
        ui.onNodeWithTag("design_input").performClick().assertIsFocused()
        val layouts = mutableListOf<TextLayoutResult>()
        ui.onNodeWithText("收款人", useUnmergedTree = true)
            .performSemanticsAction(SemanticsActions.GetTextLayoutResult) { assertTrue(it(layouts)) }
        val labelColor = layouts.last().layoutInput.style.color
        assertNotEquals("The rendered label must resolve a real foreground color", Color.Unspecified, labelColor)
        // Read the rendered container, not the theme token, so a missing field
        // override or accidental inherited background fails this regression.
        val pixels = ui.onNodeWithTag("design_input").captureToImage().toPixelMap()
        val background = pixels[pixels.width / 2, pixels.height - (8 * scale).toInt()]
        val lighter = maxOf(labelColor.luminance(), background.luminance())
        val darker = minOf(labelColor.luminance(), background.luminance())
        assertTrue("Focused label contrast must stay at least 4.5:1", (lighter + 0.05f) / (darker + 0.05f) >= 4.5f)

        ui.onNodeWithTag("design_input").performTextInputSelection(TextRange(0, 2))
        ui.runOnIdle { assertEquals(TextRange(0, 2), value.value.selection) }
        ui.onNodeWithTag("design_input").assertTextContains("李四")
        screenshot(if (dark) "input-dark-selected" else "input-light-selected")
        ui.onNodeWithTag("design_input").performTextReplacement("张三")
        ui.runOnIdle { assertEquals("张三", value.value.text) }
    }

    private fun assertNoTextOverflow(text: String) {
        ui.onNodeWithText(text, useUnmergedTree = true).assertTextFullyVisible(text)
    }

    private fun render(dark: Boolean, content: @Composable () -> Unit) {
        ui.setContent {
            CompositionLocalProvider(
                LocalDensity provides Density(LocalDensity.current.density, 1.6f),
            ) {
                BankAgentTheme(darkTheme = dark) {
                    CompositionLocalProvider(LocalBankMotionEnabled provides false) {
                        Surface(Modifier.size(320.dp, 480.dp), color = MaterialTheme.colorScheme.background) { content() }
                    }
                }
            }
        }
    }

    private fun screenshot(name: String) {
        ui.waitForIdle()
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val extra = InstrumentationRegistry.getArguments().getString("additionalTestOutputDir")
        val folder = if (extra != null) File(extra, "design-inputs")
            else File(instrumentation.targetContext.getExternalFilesDir(null), "design-inputs")
        check(folder.isDirectory || folder.mkdirs())
        val bitmap = ui.onRoot().captureToImage().asAndroidBitmap()
        try {
            File(folder, "$name.png").outputStream().use { check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) }
        } finally {
            bitmap.recycle()
        }
    }
}
