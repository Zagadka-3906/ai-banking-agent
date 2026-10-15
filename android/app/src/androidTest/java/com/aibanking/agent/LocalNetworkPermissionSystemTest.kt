package com.aibanking.agent

import android.accessibilityservice.AccessibilityServiceInfo
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.os.Build
import android.os.SystemClock
import android.view.accessibility.AccessibilityNodeInfo
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextReplacement
import androidx.core.content.ContextCompat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.auth.LOCAL_NETWORK_PERMISSION
import com.aibanking.agent.ui.chat.ChatViewModel
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.RuleChain
import org.junit.rules.TestRule
import org.junit.runner.RunWith
import org.junit.runners.model.Statement
import java.io.File
import java.net.URI

/**
 * Real Android 17 permission dialog + production Activity + isolated HTTP login.
 *
 * Opt in with networkPermissionTest=true on a fresh/cleared emulator app. Build
 * Debug with Core http://10.0.2.2:18800/ and Gateway http://10.0.2.2:18810/;
 * scenario uses loopback and intentionally does not need LAN permission.
 * Do not grant permissions automatically for this test run.
 */
@RunWith(AndroidJUnit4::class)
class LocalNetworkPermissionSystemTest {
    private val ui = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val arguments get() = InstrumentationRegistry.getArguments()

    // Check the explicit opt-in before the Activity rule launches any app UI.
    @get:Rule val rules: TestRule = RuleChain.outerRule(TestRule { base, _ ->
        object : Statement() {
            override fun evaluate() {
                assumeTrue("Requires an explicit isolated permission test run", arguments.getString("networkPermissionTest") == "true")
                assumeTrue("Requires Android 17 or later", Build.VERSION.SDK_INT >= 37)
                assumeTrue("Scenario loopback endpoints bypass LAN permission", !BuildConfig.APPLICATION_ID.endsWith(".scenariotest"))
                assumeTrue("Core must be the isolated emulator host endpoint", isIsolatedEndpoint(BuildConfig.CORE_BASE_URL, 18800))
                assumeTrue("Gateway must be the isolated emulator host endpoint", isIsolatedEndpoint(BuildConfig.GATEWAY_BASE_URL, 18810))
                base.evaluate()
            }
        }
    }).around(ui)

    @Test fun system_denial_then_grant_unblocks_viewmodel_creation_and_real_login() {
        val automation = instrumentation.uiAutomation
        val serviceInfo = automation.serviceInfo
        val originalFlags = serviceInfo.flags
        serviceInfo.flags = originalFlags or AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
        automation.serviceInfo = serviceInfo
        try {
            assertFalse("Start with cleared app data and no pre-granted nearby-device permission", permissionGranted())
            ui.onNodeWithText("允许连接本地服务").assertIsDisplayed().assertIsEnabled()
            assertChatViewModelPresent(expected = false)
            saveScreenshot("01-before-permission")

            ui.onNodeWithText("允许连接本地服务").performClick()
            clickSystemPermissionButton(allow = false)
            ui.waitUntil(15_000) { ui.onAllNodesWithText("重新授权").fetchSemanticsNodes().size == 1 }
            assertFalse("Denying the system dialog must leave LAN access blocked", permissionGranted())
            assertChatViewModelPresent(expected = false)
            ui.onNodeWithTag(UiTags.LOGIN_BUTTON).assertDoesNotExist()
            saveScreenshot("02-denied-without-viewmodel")

            ui.onNodeWithText("重新授权").assertIsEnabled().performClick()
            clickSystemPermissionButton(allow = true)
            waitOutsideCompose("The OS must report LAN permission granted") { permissionGranted() }
            ui.waitUntil(20_000) { ui.onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().size == 1 }
            ui.onNodeWithTag("local_network_permission_action").assertDoesNotExist()
            assertChatViewModelPresent(expected = true)
            saveScreenshot("03-granted-login-ready")

            val password = arguments.getString("networkPermissionPassword") ?: "Bank@2026"
            ui.onNodeWithTag(UiTags.LOGIN_USER).performScrollTo().performTextReplacement("user_zhang")
            ui.onNodeWithTag(UiTags.LOGIN_PASSWORD).performScrollTo().performTextReplacement(password)
            // Settle the keyboard before scrolling to a button near the bottom.
            androidx.test.espresso.Espresso.closeSoftKeyboard()
            ui.waitForIdle()
            ui.onNodeWithTag(UiTags.LOGIN_BUTTON).performScrollTo().assertIsEnabled().performClick()
            ui.waitUntil(30_000) { ui.onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().size == 1 }
            ui.onNodeWithTag(UiTags.CHAT_INPUT).assertIsDisplayed()
            assertTrue(permissionGranted())
            saveScreenshot("04-real-login-after-permission")
        } finally {
            serviceInfo.flags = originalFlags
            automation.serviceInfo = serviceInfo
        }
    }

    private fun permissionGranted(): Boolean =
        ContextCompat.checkSelfPermission(instrumentation.targetContext, LOCAL_NETWORK_PERMISSION) == PackageManager.PERMISSION_GRANTED

    @SuppressLint("RestrictedApi") // Read existing keys only; never ask a provider to create a ViewModel.
    private fun assertChatViewModelPresent(expected: Boolean) {
        ui.activityRule.scenario.onActivity { activity ->
            val keys = activity.viewModelStore.keys().map { it.toString() }
            val present = keys.any { it.contains(ChatViewModel::class.java.name) }
            assertTrue("Expected ChatViewModel present=$expected; existing keys=$keys", present == expected)
        }
    }

    private fun clickSystemPermissionButton(allow: Boolean) {
        var clicked = false
        waitOutsideCompose("Expected an enabled ${if (allow) "allow" else "deny"} button in the system permission dialog") {
            val root = instrumentation.uiAutomation.rootInActiveWindow ?: return@waitOutsideCompose false
            val owner = root.packageName?.toString().orEmpty()
            if (owner !in setOf("com.android.permissioncontroller", "com.google.android.permissioncontroller")) {
                return@waitOutsideCompose false
            }
            val button = findPermissionButton(root, allow) ?: return@waitOutsideCompose false
            clicked = button.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            clicked
        }
        assertTrue("The system permission button click must be accepted", clicked)
        waitOutsideCompose("The system permission dialog must close after the choice") {
            val owner = instrumentation.uiAutomation.rootInActiveWindow?.packageName?.toString().orEmpty()
            owner == BuildConfig.APPLICATION_ID
        }
    }

    private fun findPermissionButton(node: AccessibilityNodeInfo, allow: Boolean): AccessibilityNodeInfo? {
        val id = node.viewIdResourceName?.substringAfter(":id/")
        val ids = if (allow) setOf("permission_allow_button", "permission_allow_foreground_only_button")
            else setOf("permission_deny_button")
        if (id in ids && node.isClickable && node.isEnabled && node.isVisibleToUser) return node
        repeat(node.childCount) { index ->
            node.getChild(index)?.let { child -> findPermissionButton(child, allow)?.let { return it } }
        }
        return null
    }

    private fun waitOutsideCompose(message: String, timeoutMillis: Long = 15_000, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + timeoutMillis
        while (!condition()) {
            if (SystemClock.uptimeMillis() >= deadline) throw AssertionError(message)
            SystemClock.sleep(50)
        }
    }

    private fun saveScreenshot(name: String) {
        val root = arguments.getString("additionalTestOutputDir")?.let(::File)
            ?: instrumentation.targetContext.getExternalFilesDir(null) ?: return
        val directory = File(root, "local-network-permission").apply { mkdirs() }
        instrumentation.uiAutomation.takeScreenshot()?.let { bitmap ->
            File(directory, "$name.png").outputStream().use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
            bitmap.recycle()
        }
    }

    private fun isIsolatedEndpoint(value: String, port: Int): Boolean = runCatching {
        val uri = URI(value)
        uri.scheme == "http" && uri.host == "10.0.2.2" && uri.port == port
    }.getOrDefault(false)
}
