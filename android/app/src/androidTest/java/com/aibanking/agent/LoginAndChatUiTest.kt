package com.aibanking.agent

import androidx.compose.ui.test.ComposeTimeoutException
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.printToLog
import androidx.lifecycle.ViewModelProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.data.SessionStore
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.ChatRole
import com.aibanking.agent.ui.chat.ChatViewModel
import org.junit.Assert.assertTrue
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * End-to-end UI tests, run on a device or emulator.
 *
 * These exist because the shell cannot drive this app: `adb shell input text`
 * only sends ASCII, and every instruction the agent understands is Chinese.
 * An instrumented test types real text and performs real clicks, so it
 * exercises the parts that only compiling cannot prove - the Compose tree, the
 * streaming client, the Keystore enrolment and the approval flow.
 *
 * The debug app uses the local integration services via 10.0.2.2.
 * Requirements:
 *
 *  * the banking stack running on the host, reachable as 10.0.2.2:8000 (core)
 *    and 10.0.2.2:8100 (gateway);
 *  * the demo accounts seeded (password ``Bank@2026``);
 *  * **a fresh core per test class.**  The core's ledger is in-memory and the
 *    daily risk accumulator is cumulative, so a transfer made by one class
 *    pushes the next one over the 1000-yuan yellow/red boundary - the yellow
 *    test would then be handed a red approval and time out waiting for a
 *    settlement nobody authorized.  Restart the core between classes:
 *
 *        gradlew connectedDebugAndroidTest \
 *          -Pandroid.testInstrumentationRunnerArguments.class=com.aibanking.agent.LoginAndChatUiTest
 *
 * Run with::
 *
 *     gradlew connectedDebugAndroidTest
 */
@RunWith(AndroidJUnit4::class)
class LoginAndChatUiTest {

    @get:Rule
    val ui = createAndroidComposeRule<MainActivity>()


    /**
     * Log in unless a previous test already did.
     *
     * The session credential is deliberately persisted (encrypted) so a restart
     * does not force a re-login, which means the second test in this class can
     * start on the chat screen already.
     */
    private fun ensureLoggedIn() = ui.ensureLoggedIn(DEMO_PASSWORD, LOGIN_TIMEOUT_MS)

    private fun freshLogin() {
        lateinit var model: ChatViewModel
        ui.runOnIdle { model = ViewModelProvider(ui.activity)[ChatViewModel::class.java] }
        ui.waitUntil(LOGIN_TIMEOUT_MS) { !model.state.value.restoring }
        if (model.state.value.authenticated) ui.runOnIdle { model.logout() }
        ui.waitUntil(LOGIN_TIMEOUT_MS) { !model.state.value.authenticated && !model.state.value.busy }
        ensureLoggedIn()
    }

    private fun send(text: String) = ui.sendChatMessage(text)

    /** Any ¥-formatted figure; the welcome text contains none. */
    private fun moneyIsVisible(): Boolean =
        ui.onAllNodesWithText("¥", substring = true).fetchSemanticsNodes().isNotEmpty()

    private lateinit var observedViewModel: ChatViewModel

    /** Every audit trace id in the transcript, including off-screen lazy items. */
    private fun messageTraceIds(): List<String> {
        if (!::observedViewModel.isInitialized) {
            ui.runOnIdle { observedViewModel = ViewModelProvider(ui.activity)[ChatViewModel::class.java] }
        }
        return observedViewModel.state.value.messages.mapNotNull { it.traceId?.ifBlank { null } }
    }

    /** Wait for the trace id of *this* turn: the first one that was not there before. */
    private fun awaitNewTraceId(previous: List<String>): String {
        var found: String? = null
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            found = messageTraceIds().lastOrNull { it !in previous }
            found != null
        }
        return checkNotNull(found) { "no new audit trace id appeared for this turn" }
    }

    /** Ask the core what it recorded for one correlation id. */
    private fun fetchCoreTrace(traceId: String): String = fetchCore("security/audit/trace/$traceId")

    private fun fetchCore(path: String): String {
        val credential = checkNotNull(SessionStore(InstrumentationRegistry.getInstrumentation().targetContext).sessionToken())
        val connection = (URL("$CORE_BASE/api/v1/$path").openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            // The endpoint scopes a non-admin caller to their own requests.
            setRequestProperty("X-User-Id", PRINCIPAL)
            setRequestProperty("X-Session-Token", credential)
            connectTimeout = 10_000
            readTimeout = 10_000
        }
        return connection.inputStream.bufferedReader().use { it.readText() }
    }

    @Test
    fun login_enters_chat_before_optional_device_bootstrap_finishes() {
        ui.waitUntil(timeoutMillis = LOGIN_TIMEOUT_MS) {
            ui.onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().isNotEmpty() ||
                ui.onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().isNotEmpty()
        }
        if (ui.onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().isNotEmpty()) {
            ui.openFromDrawer(UiTags.LOGOUT)
            ui.waitUntil(timeoutMillis = LOGIN_TIMEOUT_MS) {
                ui.onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().size == 1
            }
        }

        ui.onNodeWithTag(UiTags.LOGIN_PASSWORD).performScrollTo().performTextInput(DEMO_PASSWORD)
        ui.submitLoginForm()

        // Device enrollment and FCM token acquisition continue in the
        // background. A valid session must navigate immediately instead of
        // requiring a process restart to restore the persisted credential.
        ui.waitUntil(timeoutMillis = 10_000L) {
            ui.onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().size == 1
        }
    }

    @Test
    fun a_read_only_turn_returns_a_verified_reply_with_structured_facts() {
        ensureLoggedIn()
        val before = messageTraceIds()
        send("查一下余额")

        // A trace id that was not on screen before is this turn's reply: it proves
        // login, the streamed turn, the core round-trip and the result view all
        // worked - without depending on which test ran first.
        val traceId = awaitNewTraceId(before)

        // The structured half is what renders the amount, so a ¥ figure must be
        // there too.
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) { moneyIsVisible() }

        val trace = fetchCoreTrace(traceId)
        assertTrue("core trace for $traceId was: $trace", trace.contains("BALANCE_QUERY"))
    }

    @Test
    fun a_bare_wealth_message_returns_products_without_internal_steps() {
        ensureLoggedIn()
        val products = JSONObject(fetchCore("wealth/products")).getJSONArray("data")
        val expectedIds = (0 until products.length()).map { products.getJSONObject(it).getString("product_id") }.toSet()
        assertTrue("the fixture must contain products to browse", expectedIds.isNotEmpty())
        val firstName = products.getJSONObject(0).getString("name")
        lateinit var model: ChatViewModel
        ui.runOnIdle { model = ViewModelProvider(ui.activity)[ChatViewModel::class.java] }
        val previousIds = model.state.value.messages.map { it.id }.toSet()
        send("理财")

        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            model.state.value.messages.any { it.id !in previousIds && productIds(it.result) == expectedIds } &&
                ui.onAllNodesWithText(firstName, substring = true, useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty()
        }
        assertTrue(
            "internal processing steps must never be rendered in a reply card",
            ui.onAllNodesWithText("处理步骤", useUnmergedTree = true).fetchSemanticsNodes().isEmpty(),
        )
    }

    private fun productIds(value: Any?): Set<String> = when (value) {
        is Map<*, *> -> {
            val own = (value["product_id"] as? String)?.takeIf { value["name"] is String && value["min_amount"] is Number }
            setOfNotNull(own) + value.values.flatMap { productIds(it) }
        }
        is List<*> -> value.flatMap { productIds(it) }.toSet()
        else -> emptySet()
    }

    @Test
    fun predicted_quick_option_sends_immediately() {
        freshLogin()
        lateinit var viewModel: ChatViewModel
        lateinit var prompt: String
        var previousMatches = 0
        ui.runOnIdle {
            viewModel = ViewModelProvider(ui.activity)[ChatViewModel::class.java]
            prompt = viewModel.state.value.suggestions.first()
            previousMatches = viewModel.state.value.messages.count {
                it.role == ChatRole.USER && it.text == prompt
            }
        }
        ui.waitUntil(timeoutMillis = LOGIN_TIMEOUT_MS) {
            ui.onAllNodesWithTag(UiTags.QUICK_SUGGESTIONS).fetchSemanticsNodes().isNotEmpty()
        }

        ui.onNodeWithTag(UiTags.quickSuggestion(0)).performClick()

        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            viewModel.state.value.messages.count {
                it.role == ChatRole.USER && it.text == prompt
            } > previousMatches
        }
        ui.runOnIdle { assertTrue(viewModel.state.value.input.isEmpty()) }
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) { !viewModel.state.value.busy }
        // A useful follow-up is optional; an empty reviewed result is valid.
    }

    @Test
    fun subscription_query_does_not_suggest_cancellation_but_explicit_requests_still_work() {
        ensureLoggedIn()
        send("查看我的订阅")
        lateinit var viewModel: ChatViewModel
        ui.runOnIdle { viewModel = ViewModelProvider(ui.activity)[ChatViewModel::class.java] }

        // Yellow tier: the subscription list is released only after confirmation.
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            !viewModel.state.value.busy && viewModel.state.value.pending != null
        }
        ui.runOnIdle {
            assertTrue(viewModel.state.value.pending?.summary?.contains("订阅") == true)
            viewModel.approve()
        }
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            !viewModel.state.value.busy && viewModel.state.value.pending == null
        }
        ui.runOnIdle { assertTrue(viewModel.state.value.suggestions.none { it.startsWith("取消") }) }
        send("取消 Apple 订阅")
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            !viewModel.state.value.busy && viewModel.state.value.pending != null
        }
        ui.runOnIdle {
            val state = viewModel.state.value
            assertTrue(state.messages.any { it.role == ChatRole.USER && it.text == "取消 Apple 订阅" })
            assertTrue(state.pending?.summary?.contains("Apple") == true)
            viewModel.reject()
        }
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            !viewModel.state.value.busy && viewModel.state.value.pending == null
        }
    }

    @Test
    fun typing_hides_suggestions_and_stale_taps_cannot_erase_the_draft() {
        freshLogin()
        lateinit var viewModel: ChatViewModel
        lateinit var prompt: String
        var messageCount = 0
        ui.runOnIdle {
            viewModel = ViewModelProvider(ui.activity)[ChatViewModel::class.java]
            prompt = viewModel.state.value.suggestions.first()
            messageCount = viewModel.state.value.messages.size
            viewModel.onInputChange("我正在编辑的问题")
        }
        ui.onNodeWithTag(UiTags.QUICK_SUGGESTIONS).assertDoesNotExist()
        ui.runOnIdle {
            viewModel.onSuggestionClick(prompt)
            assertEquals("我正在编辑的问题", viewModel.state.value.input)
            assertEquals(messageCount, viewModel.state.value.messages.size)
            viewModel.onInputChange("")
            viewModel.onSuggestionClick("一条已经过期的推荐")
            assertEquals(messageCount, viewModel.state.value.messages.size)
        }
        ui.onNodeWithTag(UiTags.QUICK_SUGGESTIONS).assertIsDisplayed()
    }

    @Test
    fun a_yellow_transfer_waits_for_confirmation_and_then_settles() {
        ensureLoggedIn()
        send("给李四转 300 元")

        // The core owns the risk tier; the app must show the approval it asked for.
        try {
            ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
                ui.onAllNodesWithTag(UiTags.APPROVAL_APPROVE).fetchSemanticsNodes().isNotEmpty()
            }
        } catch (timeout: ComposeTimeoutException) {
            // Say what was on screen instead of only that something was missing.
            ui.onRoot().printToLog("YELLOWTIER")
            throw timeout
        }
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().performClick()

        // The card goes away only once the core has settled the transfer.
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            ui.onAllNodesWithTag(UiTags.APPROVAL_APPROVE).fetchSemanticsNodes().isEmpty()
        }
        // A settled transfer opens its result page; the conversation is checked behind it.
        ui.returnFromOutcome(settleMs = TURN_TIMEOUT_MS)
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) { moneyIsVisible() }
        ui.runOnIdle {
            val currentState = ViewModelProvider(ui.activity)[ChatViewModel::class.java].state.value
            assertTrue(
                "settled transfers must not retain their approval preview",
                currentState.messages.none { it.approvalPreview || it.text.trimStart().startsWith("您正准备进行：") },
            )
            // No rule-based guesses after a settled turn; only model chips may follow.
            assertTrue("rule-based follow-ups must not appear", "查看账户余额" !in currentState.suggestions)
        }
        assertTrue(
            ui.onAllNodesWithText("您正准备进行", substring = true, useUnmergedTree = true)
                .fetchSemanticsNodes().isEmpty(),
        )
    }

    private companion object {
        const val DEMO_PASSWORD = "Bank@2026"
        const val LOGIN_TIMEOUT_MS = 30_000L
        const val TURN_TIMEOUT_MS = 45_000L
        const val PRINCIPAL = "user_zhang"
        val CORE_BASE = BuildConfig.CORE_BASE_URL.trimEnd('/')
    }
}

/**
 * The red authorization tier, driven through the real UI.
 *
 * This is the only place the *device* half of the trust root is exercised: the
 * app asks the core for a liveness challenge, signs it with the P-256 key inside
 * the Android Keystore, and the core verifies that signature before settling.
 * The Python probe covers the same protocol with a software key, but only this
 * test proves the Keystore path - key generation, signing and the wire format -
 * works on a real Android runtime.
 *
 * The local biometric gate is satisfied with the device credential (a PIN set
 * with `adb shell locksettings set-pin`), because an emulator has no enrolled
 * fingerprint.  That prompt is a *system* window, so it cannot be answered from
 * inside this process; the companion script
 * `android/tools/satisfy-biometric-prompt.ps1` types the PIN from the host while
 * this test waits.
 */
@RunWith(AndroidJUnit4::class)
class RedTierUiTest {

    @get:Rule
    val ui = createAndroidComposeRule<MainActivity>()
    @Test
    fun a_red_transfer_needs_the_second_factor_and_settles_with_a_device_signature() {
        ui.ensureLoggedIn(DEMO_PASSWORD, LOGIN_TIMEOUT_MS)
        ui.sendChatMessage("给李四转 2000 元")

        try {
            ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
                ui.onAllNodesWithTag(UiTags.APPROVAL_APPROVE).fetchSemanticsNodes().isNotEmpty()
            }
        } catch (timeout: ComposeTimeoutException) {
            // A bare timeout says only that something is missing.  Dumping the
            // tree makes the failure report what was actually on screen.
            ui.onRoot().printToLog("REDTIER")
            throw timeout
        }
        // The red tier must demand both factors, so the card carries an SMS field.
        assertTrue(
            "red approval must ask for an SMS code",
            ui.onAllNodesWithTag(UiTags.APPROVAL_OTP).fetchSemanticsNodes().isNotEmpty(),
        )

        ui.enterApprovalOtp(OTP)
        // A disabled button makes the click below a silent no-op, which would
        // surface only as a confusing timeout.  Fail here instead, naming the
        // reason: no usable authenticator on this device.
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).assertIsEnabled()
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().performClick()

        // A disabled button (no usable authenticator on the device) would make
        // this a silent no-op; assert we got past the gate instead.
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            ui.onAllNodesWithTag(UiTags.APPROVAL_APPROVE).fetchSemanticsNodes().isEmpty()
        }
        ui.returnFromOutcome(settleMs = TURN_TIMEOUT_MS)
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            ui.onAllNodesWithText("¥", substring = true).fetchSemanticsNodes().isNotEmpty()
        }
        ui.runOnIdle {
            val state = ViewModelProvider(ui.activity)[ChatViewModel::class.java].state.value
            assertTrue("the red operation must have a verified successful receipt",
                state.messages.any { it.businessStatus == "SUCCEEDED" && it.riskLevel == "RED" })
        }
    }

    private companion object {
        const val DEMO_PASSWORD = "Bank@2026"
        const val OTP = "888888"
        const val LOGIN_TIMEOUT_MS = 30_000L
        const val TURN_TIMEOUT_MS = 60_000L
    }
}
