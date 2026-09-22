package com.aibanking.agent

import android.Manifest
import android.graphics.Bitmap
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.Density
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.rule.GrantPermissionRule
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.auth.LoginScreen
import com.aibanking.agent.ui.theme.BankAgentTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.RuleChain
import org.junit.rules.TestRule
import org.junit.runner.RunWith
import java.io.File

/** No backend: exercise the redesigned form with large text and dark colors. */
@RunWith(AndroidJUnit4::class)
class LoginLayoutTest {
    @get:Rule val ui = createComposeRule()

    @Test fun large_text_form_scrolls_and_submits_the_entered_credentials() {
        var submitted: Triple<String, String, String?>? = null
        ui.setContent {
            val density = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(density.density, 1.5f)) {
                BankAgentTheme(darkTheme = true) {
                    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                        LoginScreen(false, true, null, {}, { user, password, otp -> submitted = Triple(user, password, otp) })
                    }
                }
            }
        }
        ui.onNodeWithTag(UiTags.LOGIN_BUTTON).performScrollTo().assertIsNotEnabled()
        ui.onNodeWithTag(UiTags.LOGIN_PASSWORD).performScrollTo().performTextInput("Bank@2026")
        ui.onNodeWithTag(UiTags.LOGIN_OTP).performScrollTo().performTextInput("888888")
        ui.onNodeWithTag(UiTags.LOGIN_BUTTON).performScrollTo().assertIsEnabled().performClick()
        ui.runOnIdle { assertEquals(Triple("user_zhang", "Bank@2026", "888888"), submitted) }
    }

    @Test fun busy_form_cannot_submit_a_second_request() {
        var submitted: String? = null
        ui.setContent {
            BankAgentTheme {
                Box(Modifier.fillMaxSize()) {
                    LoginScreen(true, false, "登录暂不可用", {}, { user, _, _ -> submitted = user })
                }
            }
        }
        ui.onNodeWithTag(UiTags.LOGIN_BUTTON).performScrollTo().assertIsNotEnabled()
        ui.onNodeWithTag(UiTags.LOGIN_PASSWORD).performScrollTo().assertIsNotEnabled()
        ui.runOnIdle { assertNull(submitted) }
    }
}

/** Uses the local demo backend, as the existing navigation tests do. */
@RunWith(AndroidJUnit4::class)
class UiRefactorSmokeTest {
    private val ui = createAndroidComposeRule<MainActivity>()
    private val notifications = GrantPermissionRule.grant(Manifest.permission.POST_NOTIFICATIONS)
    @get:Rule val rules: TestRule = RuleChain.outerRule(notifications).around(ui)

    @Test fun contact_form_search_and_transfer_shortcut_work_together() {
        ui.ensureLoggedIn("Bank@2026")
        ui.onNodeWithTag(UiTags.OPEN_CONTACTS).performClick()
        ui.waitUntil(20_000) { ui.onAllNodesWithTag(UiTags.CONTACT_SEARCH).fetchSemanticsNodes().isNotEmpty() }
        ui.onNodeWithTag(UiTags.CONTACT_OPEN_ADD).performClick()
        ui.waitUntil(20_000) { runCatching { ui.onNodeWithText("姓名").assertIsEnabled() }.isSuccess }
        ui.onNodeWithTag(UiTags.CONTACT_ADD).performScrollTo().assertIsNotEnabled()
        screenshot("08-contact-add")
        val suffix = System.currentTimeMillis().toString().takeLast(8)
        val name = "验收联系人$suffix"
        ui.onNodeWithText("姓名").performScrollTo().performTextInput(name)
        ui.onNodeWithText("电话").performScrollTo().performTextInput("139$suffix")
        ui.onNodeWithTag(UiTags.CONTACT_ADD).performScrollTo().assertIsEnabled().performClick()
        ui.waitUntil(20_000) { ui.onAllNodesWithTag(UiTags.CONTACT_SEARCH).fetchSemanticsNodes().isNotEmpty() }
        ui.onNodeWithTag(UiTags.CONTACT_SEARCH).performTextInput(name)
        ui.onNode(hasText(name) and !hasSetTextAction()).performClick()
        ui.onNodeWithTag(UiTags.CONTACT_TRANSFER).performScrollTo().assertIsEnabled()
        screenshot("09-contact-detail")
        ui.onNodeWithTag(UiTags.CONTACT_TRANSFER).performClick()
        ui.waitUntil(10_000) { ui.onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().isNotEmpty() }
        org.junit.Assert.assertTrue(ui.chatInputText()?.contains(name) == true)
    }

    @Test fun all_destinations_remain_reachable_and_preserve_the_chat_draft() {
        ui.ensureLoggedIn("Bank@2026")
        ui.onNodeWithTag(UiTags.LOGOUT).performClick()
        ui.waitUntil(15_000) { ui.onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().isNotEmpty() }
        screenshot("01-login")
        ui.ensureLoggedIn("Bank@2026")
        ui.waitUntil(8_000) { ui.onAllNodesWithText("本机设备已注册，高风险操作可用").fetchSemanticsNodes().isEmpty() }
        screenshot("02-assistant")
        ui.onNodeWithTag(UiTags.CHAT_INPUT).performTextInput("查一下余额")
        ui.activityRule.scenario.onActivity { activity ->
            val keyboard = activity.getSystemService(android.content.Context.INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager
            keyboard.hideSoftInputFromWindow(activity.window.decorView.windowToken, 0)
        }
        ui.waitUntil(5_000) { ui.onAllNodesWithTag(UiTags.OPEN_CARDS).fetchSemanticsNodes().isNotEmpty() }
        ui.waitForIdle()
        Thread.sleep(450)
        ui.onNodeWithTag(UiTags.OPEN_CARDS).assertIsDisplayed().performClick()
        ui.waitUntil(20_000) { ui.onAllNodesWithTag(UiTags.CARD_LIST).fetchSemanticsNodes().isNotEmpty() }
        screenshot("03-cards")
        ui.onNodeWithTag(UiTags.OPEN_WEALTH).performClick()
        ui.waitUntil(20_000) { ui.onAllNodesWithTag(UiTags.WEALTH_LIST).fetchSemanticsNodes().isNotEmpty() }
        screenshot("04-wealth")
        ui.onNodeWithTag(UiTags.WEALTH_LIST).performScrollToNode(hasTestTag(UiTags.wealthProduct("ZY18031")))
        ui.onNodeWithTag(UiTags.wealthProduct("ZY18031")).performClick()
        ui.onNodeWithTag(UiTags.WEALTH_DETAIL).assertExists()
        screenshot("05-wealth-detail")
        ui.onNodeWithTag(UiTags.OPEN_CONTACTS).performClick()
        ui.waitUntil(20_000) { ui.onAllNodesWithTag(UiTags.CONTACT_SEARCH).fetchSemanticsNodes().isNotEmpty() }
        screenshot("06-contacts")
        ui.onNodeWithTag("open_assistant").performClick()
        ui.waitUntil(10_000) { ui.onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().isNotEmpty() }
        assertEquals("查一下余额", ui.chatInputText())
        ui.onNodeWithTag(UiTags.OPEN_DEVICES).performClick()
        ui.onNodeWithText("已注册设备").assertExists()
        screenshot("07-devices")
    }

    private fun screenshot(name: String) {
        ui.waitForIdle()
        // Let the last rendered frame catch up with the Compose test clock.
        Thread.sleep(450)
        ui.waitUntil(8_000) { ui.onAllNodesWithText("本机设备已注册，高风险操作可用").fetchSemanticsNodes().isEmpty() }
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val folder = File(instrumentation.targetContext.getExternalFilesDir(null), "ui-review").apply { mkdirs() }
        val bitmap = instrumentation.uiAutomation.takeScreenshot()
        File(folder, "$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        bitmap.recycle()
    }
}
