package com.aibanking.agent

import android.os.SystemClock
import android.os.Bundle
import android.view.KeyEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.inputmethod.InputMethodManager
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.data.SessionStore
import com.aibanking.agent.security.BiometricGate
import com.aibanking.agent.security.DeviceKeyManager
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.ChatMessage
import com.aibanking.agent.ui.chat.ChatUiState
import com.aibanking.agent.ui.chat.ChatViewModel
import com.google.gson.Gson
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicInteger

/**
 * UI + real ViewModel + HTTP/SSE + Android Keystore acceptance tests.
 *
 * The isolated backend holds a real response after its first SSE stage has
 * reached the app. Releasing it after another login exercises the blocking
 * stream reader as well as cancellation. No production repository is mocked.
 *
 * Callback cases capture the exact callbacks ChatScreen passes to BiometricGate.
 * Separate device-credential cases use the real Android system prompt with a
 * PIN enrolled on an isolated emulator. Neither simulates a fingerprint sensor.
 */
@BankingRegression
class ScenarioAccountIsolationUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val controlToken get() = checkNotNull(InstrumentationRegistry.getArguments().getString("controlToken"))
    private val store get() = SessionStore(instrumentation.targetContext)
    private lateinit var model: ChatViewModel
    private val held = mutableListOf<Pair<Int, String>>()

    @Before fun reset() {
        assumeTrue(BuildConfig.APPLICATION_ID.endsWith(".scenariotest"))
        ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
        ui.waitUntil(20_000) { !model.state.value.restoring }
        if (model.state.value.authenticated) {
            ui.runOnIdle { model.dismissPanels() }
            logout()
        }
        control(GATEWAY, "reset", JSONObject())
        control(CORE, "reset", JSONObject())
        login(ALICE)
    }

    @After fun releaseOutstandingResponses() {
        held.forEach { (port, id) ->
            runCatching { control(port, "release", JSONObject().put("id", id)) }
        }
    }

    @Test fun delayed_voice_draft_is_discarded_after_account_switch() {
        lateinit var result: (String) -> Unit
        ui.runOnIdle { result = model.captureVoiceResult() }
        logout()
        login(BOB)
        ui.runOnIdle { result("给李四转2000元") }
        assertEquals("", model.state.value.input)
        assertNull(model.state.value.pending)
        queryBalance()
    }

    @Test fun voice_result_cannot_replace_later_manual_edits_or_auto_send() {
        lateinit var result: (String) -> Unit
        ui.runOnIdle { result = model.captureVoiceResult(); model.onInputChange("手工输入") }
        ui.runOnIdle { result("给李四转2000元") }
        assertEquals("手工输入", model.state.value.input)
        ui.runOnIdle { result = model.captureVoiceResult(); result("查询余额") }
        assertEquals("查询余额", model.state.value.input)
        assertFalse(model.state.value.busy)
        assertNull(model.state.value.pending)
        ui.runOnIdle {
            val old = model.captureVoiceResult()
            val latest = model.captureVoiceResult()
            old("给李四转2000元")
            assertEquals("查询余额", model.state.value.input)
            latest("查看卡片")
            assertEquals("查看卡片", model.state.value.input)
        }
    }

    @Test fun delayed_success_and_sse_progress_do_not_enter_another_account_or_its_storage() {
        delayedStream("success", BOB, "delayed-success")
    }

    @Test fun old_stream_401_cannot_sign_out_the_new_account() {
        delayedStream("sse_401", BOB, "old-stream-401")
    }

    @Test fun old_stream_500_cannot_show_an_error_in_the_new_account() {
        delayedStream("sse_500", BOB, "old-stream-500")
    }

    @Test fun same_account_relogin_also_rejects_the_previous_login_response() {
        delayedStream("success", ALICE, "same-account-new-login")
    }

    @Test fun old_http_401_from_a_panel_cannot_sign_out_the_new_account() {
        val id = "old-panel-401"
        hold(CORE, id, "/api/v1/tasks", "GET", "http_401")
        ui.openFromDrawer(UiTags.OPEN_TASKS)
        waitHeld(CORE, id)
        assertTrue(model.state.value.panelBusy)
        instrumentation.uiAutomation.executeShellCommand("input keyevent 4").close()
        ui.waitUntil(10_000) { !model.state.value.showTasks }
        logout()
        login(BOB)
        queryBalance()
        val credential = checkNotNull(store.sessionToken())
        StateProbe().use { probe ->
            release(CORE, id)
            assertStableFor(probe)
        }
        assertStoredSession(BOB, credential)
        assertRestoresWithFreshViewModel(BOB, credential)
        evidence(id, CORE)
    }

    @Test fun late_biometric_success_and_failure_cannot_touch_another_accounts_approval() {
        requestRedApproval("CARD-001")
        val oldApproval = checkNotNull(model.state.value.pending).approvalId
        val oldCallbacks = ui.runOnIdle { model.deviceApprovalCallbacks(oldApproval) }
        logout()
        login(BOB)
        requestRedApproval("CARD-002")
        assertNotEquals(oldApproval, checkNotNull(model.state.value.pending).approvalId)
        enterOtp()
        val credential = checkNotNull(store.sessionToken())
        val requestsBefore = approvalRequests()
        StateProbe().use { probe ->
            ui.runOnIdle { oldCallbacks.onFailure("STALE_ACCOUNT_BIOMETRIC_ERROR") }
            assertStableFor(probe, 500)
            ui.runOnIdle { oldCallbacks.onSuccess() }
            assertStableFor(probe)
        }
        assertEquals("old success must not request B's device challenge or resolve B's approval", requestsBefore, approvalRequests())
        assertStoredSession(BOB, credential)
        evidence("old-biometric-callbacks", GATEWAY)
    }

    @Test fun replaced_approval_in_the_same_login_rejects_its_old_biometric_callbacks() {
        requestRedApproval("CARD-001")
        val oldApproval = checkNotNull(model.state.value.pending).approvalId
        val oldCallbacks = ui.runOnIdle { model.deviceApprovalCallbacks(oldApproval) }
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_REJECT))
        ui.onNodeWithTag(UiTags.APPROVAL_REJECT).performClick()
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending == null }
        requestRedApproval("CARD-001")
        assertNotEquals(oldApproval, checkNotNull(model.state.value.pending).approvalId)
        enterOtp()
        val requestsBefore = approvalRequests()
        StateProbe().use { probe ->
            ui.runOnIdle { oldCallbacks.onFailure("STALE_APPROVAL_BIOMETRIC_ERROR") }
            assertStableFor(probe, 500)
            ui.runOnIdle { oldCallbacks.onSuccess() }
            assertStableFor(probe)
        }
        assertEquals(requestsBefore, approvalRequests())
        evidence("replaced-approval-callbacks", GATEWAY)
    }

    @Test fun late_system_device_credential_success_cannot_approve_the_new_accounts_request() {
        delayedSystemPrompt(success = true)
    }

    @Test fun late_system_device_credential_cancellation_cannot_error_the_new_account() {
        delayedSystemPrompt(success = false)
    }

    @Test fun current_system_device_credential_can_complete_its_own_approval() {
        requireDeviceCredential()
        logout()
        login(BOB)
        requestRedApproval("CARD-002")
        enterDynamicOtp()
        openSystemPrompt()
        finishSystemPrompt(success = true)
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending == null }
        assertNull(model.state.value.error)
        assertCardFrozen("CARD-002")
        assertTrue(approvalRequests().any { it.contains("/face-challenge") })
        evidence("current-system-credential-positive-control", GATEWAY)
    }

    @Test fun account_device_keys_are_distinct_and_stable_after_switching_back() {
        val aliceDevice = checkNotNull(store.deviceId())
        logout()
        login(BOB)
        val bobDevice = checkNotNull(store.deviceId())
        assertNotEquals(aliceDevice, bobDevice)
        logout()
        login(ALICE)
        assertEquals(aliceDevice, store.deviceId())
        logout()
        login(BOB)
        assertEquals(bobDevice, store.deviceId())
        evidence("stable-account-device-identities", CORE)
    }

    @Test fun legacy_device_session_restores_and_signs_with_its_original_key() {
        requireDeviceCredential()
        val legacyDevice = enrollLegacyKey()
        val credential = checkNotNull(store.sessionToken())
        store.saveDeviceId(legacyDevice)
        assertStoredSession(ALICE, credential)
        assertRestoresWithFreshViewModel(ALICE, credential)
        ui.waitUntil(20_000) { store.deviceId() == legacyDevice }
        requestRedApproval("CARD-001")
        enterOtp()
        openSystemPrompt()
        finishSystemPrompt(success = true)
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending == null }
        assertNull(model.state.value.error)
        assertCardFrozen("CARD-001")
        assertEquals(legacyDevice, store.deviceId())
        evidence("legacy-device-restore-and-sign", CORE)
    }

    @Test fun revoked_legacy_device_is_not_replaced_by_a_fresh_key_on_login() {
        val legacyDevice = enrollLegacyKey()
        authenticatedCore("devices/$legacyDevice/revoke", JSONObject())
        val before = authenticatedCore("devices").getJSONArray("data").length()
        logout()
        login(ALICE, waitForDevice = false)
        ui.waitUntil(20_000) { model.state.value.notice?.contains("本机设备未注册") == true }
        assertNull(store.deviceId())
        assertEquals(legacyDevice, DeviceKeyManager().existingDeviceId())
        val after = authenticatedCore("devices").getJSONArray("data")
        assertEquals(before, after.length())
        assertEquals("REVOKED", (0 until after.length()).map { after.getJSONObject(it) }
            .single { it.getString("device_id") == legacyDevice }.getString("status"))
        evidence("legacy-revocation-preserved", CORE)
    }

    private fun enrollLegacyKey(): String {
        val legacy = DeviceKeyManager()
        val registered = authenticatedCore("devices/register", JSONObject()
            .put("public_key", legacy.publicKeySpkiBase64()).put("label", "Legacy migration fixture").put("platform", "android"))
        return registered.getString("device_id").also { assertEquals(it, legacy.existingDeviceId()) }
    }

    private fun assertCardFrozen(cardId: String) {
        val cards = authenticatedCore("cards/${model.state.value.principal}").getJSONArray("data")
        assertEquals("FROZEN", (0 until cards.length()).map { cards.getJSONObject(it) }
            .single { it.getString("card_id") == cardId }.getString("status"))
    }

    private fun delayedSystemPrompt(success: Boolean) {
        requireDeviceCredential()
        requestRedApproval("CARD-001")
        enterOtp()
        val oldApproval = checkNotNull(model.state.value.pending).approvalId
        val prompt = openObservedSystemPrompt()
        // The system modal owns touch focus. Exercise the real ViewModel login
        // path underneath it, then let Android deliver the old prompt callback.
        instrumentation.runOnMainSync {
            model.logout()
            model.login(BOB, "Bank@2026")
        }
        waitOutsideCompose(30_000) {
            model.state.value.authenticated && model.state.value.principal == BOB && !store.deviceId().isNullOrBlank()
        }
        instrumentation.runOnMainSync {
            model.onInputChange("冻结卡片 CARD-002")
            model.send()
        }
        waitOutsideCompose(30_000) { !model.state.value.busy && model.state.value.pending != null }
        assertNotEquals(oldApproval, checkNotNull(model.state.value.pending).approvalId)
        assertTrue(checkNotNull(model.state.value.pending).mfaRequired)
        instrumentation.runOnMainSync { model.onOtpChange("888888") }
        assertTrue("A's system credential prompt must remain open after login B", systemPromptVisible())
        val requestsBefore = approvalRequests()
        val credential = checkNotNull(store.sessionToken())
        StateProbe().use { probe ->
            finishSystemPrompt(success)
            waitOutsideCompose(10_000) { prompt.success.get() + prompt.failure.get() > 0 }
            assertEquals(if (success) 1 else 0, prompt.success.get())
            assertEquals(if (success) 0 else 1, prompt.failure.get())
            assertStableFor(probe)
        }
        assertEquals(requestsBefore, approvalRequests())
        assertStoredSession(BOB, credential)
        evidence("old-system-credential-${if (success) "success" else "cancel"}", GATEWAY)
    }

    private fun requireDeviceCredential() {
        val pin = InstrumentationRegistry.getArguments().getString("devicePin")
        assumeTrue("devicePin argument requires an isolated emulator with that PIN enrolled", !pin.isNullOrBlank())
        require(checkNotNull(pin).matches(Regex("[0-9]{4,12}")))
        assertTrue(ui.runOnIdle { BiometricGate.canAuthenticate(ui.activity) })
    }

    private fun openSystemPrompt() {
        // The OTP keyboard can still be moving the approval card after Compose
        // becomes idle. Settle it before locating and tapping the real button.
        androidx.test.espresso.Espresso.closeSoftKeyboard()
        ui.waitForIdle()
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_APPROVE))
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().assertIsDisplayed().assertIsEnabled().performClick()
        waitOutsideCompose(15_000) { systemPromptVisible() }
    }

    private class PromptResult(val success: AtomicInteger = AtomicInteger(), val failure: AtomicInteger = AtomicInteger())

    private fun openObservedSystemPrompt(): PromptResult {
        val result = PromptResult()
        instrumentation.runOnMainSync {
            val approval = checkNotNull(model.state.value.pending)
            val callbacks = model.deviceApprovalCallbacks(approval.approvalId)
            // Match the actual approval button, which clears Compose focus and
            // hides its OTP keyboard before asking Android to authenticate.
            ui.activity.currentFocus?.clearFocus()
            (ui.activity.getSystemService(android.content.Context.INPUT_METHOD_SERVICE) as InputMethodManager)
                .hideSoftInputFromWindow(ui.activity.window.decorView.windowToken, 0)
            // Only observe the OS callback before forwarding it unchanged. The
            // positive control above opens the same prompt via the real button.
            BiometricGate.prompt(ui.activity, "确认高风险操作", approval.summary,
                onSuccess = { result.success.incrementAndGet(); callbacks.onSuccess() },
                onFailure = { reason -> result.failure.incrementAndGet(); callbacks.onFailure(reason) })
        }
        waitOutsideCompose(15_000) { systemPromptVisible() }
        return result
    }

    private fun systemPromptVisible(): Boolean {
        val owner = instrumentation.uiAutomation.rootInActiveWindow?.packageName?.toString().orEmpty()
        return owner == "com.android.systemui" || owner == "com.android.settings"
    }

    private fun finishSystemPrompt(success: Boolean) {
        assertTrue(systemPromptVisible())
        if (success) {
            val pin = checkNotNull(InstrumentationRegistry.getArguments().getString("devicePin"))
            if (android.os.Build.VERSION.SDK_INT >= 37) {
                // Android 17's credential window need not expose an editable
                // password node. Drive the real system window with hardware
                // keys after its accessibility events settle; the tests still
                // require the actual successful authentication callback.
                instrumentation.uiAutomation.waitForIdle(300, 5_000)
                assertTrue(systemPromptVisible())
                // The window can be shown before it takes key input; a lost first
                // digit leaves a wrong PIN and the prompt open. Let it settle and
                // type at a human pace.
                SystemClock.sleep(800)
                pin.forEach { digit ->
                    injectKey(KeyEvent.KEYCODE_0 + digit.digitToInt())
                    SystemClock.sleep(80)
                }
            } else {
                // A visible system window may appear before its EditText owns
                // input focus, which can otherwise lose the first digit.
                waitOutsideCompose(10_000) { credentialField()?.isFocused == true }
                val field = checkNotNull(credentialField())
                assertTrue(field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, pin)
                }))
                waitOutsideCompose(5_000) { credentialField()?.text?.length == pin.length }
            }
            injectKey(KeyEvent.KEYCODE_ENTER)
        } else {
            // On API 35 the first Back can dismiss only the PIN keyboard.
            injectKey(KeyEvent.KEYCODE_BACK)
            SystemClock.sleep(400)
            if (systemPromptVisible()) injectKey(KeyEvent.KEYCODE_BACK)
        }
        waitOutsideCompose(15_000) { !systemPromptVisible() }
    }

    private fun credentialField(): AccessibilityNodeInfo? {
        fun find(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            if (node == null) return null
            if (node.isEditable && node.isPassword && node.isVisibleToUser) return node
            repeat(node.childCount) { index -> find(node.getChild(index))?.let { return it } }
            return null
        }
        return find(instrumentation.uiAutomation.rootInActiveWindow)
    }

    private fun injectKey(code: Int) {
        val downTime = SystemClock.uptimeMillis()
        assertTrue(instrumentation.uiAutomation.injectInputEvent(KeyEvent(downTime, downTime, KeyEvent.ACTION_DOWN, code, 0), true))
        assertTrue(instrumentation.uiAutomation.injectInputEvent(KeyEvent(downTime, SystemClock.uptimeMillis(), KeyEvent.ACTION_UP, code, 0), true))
    }

    private fun waitOutsideCompose(timeout: Long, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + timeout
        while (!condition()) {
            if (SystemClock.uptimeMillis() >= deadline) throw AssertionError("timed out waiting for the system prompt or background login")
            SystemClock.sleep(50)
        }
    }

    private fun delayedStream(mode: String, nextPrincipal: String, id: String) {
        hold(GATEWAY, id, STREAM_PATH, "POST", mode, lateStage = "OLD_ACCOUNT_STAGE_$id")
        ui.sendChatMessage("查一下余额")
        waitHeld(GATEWAY, id)
        // Merely waiting for the HTTP request would exercise Retrofit's early
        // cancellation only. Prove the production streaming reader is active.
        ui.waitUntil(15_000) { model.state.value.messages.any { it.stages.orEmpty().isNotEmpty() } }
        assertTrue(model.state.value.busy)
        val oldMessages = model.state.value.messages.map { it.id }.toSet()
        val previousCredential = checkNotNull(store.sessionToken())
        logout()
        login(nextPrincipal)
        val credential = checkNotNull(store.sessionToken())
        assertNotEquals("even the same principal must get a separate login", previousCredential, credential)
        queryBalance()
        StateProbe().use { probe ->
            release(GATEWAY, id)
            assertStableFor(probe)
        }
        assertTrue(model.state.value.messages.none { it.id in oldMessages })
        val gate = gate(GATEWAY, id)
        assertTrue("the server must actually send the late stage", gate.getBoolean("late_stage_sent"))
        assertTrue(gate.getInt("frames_sent") >= 3)
        assertStoredSession(nextPrincipal, credential)
        assertRestoresWithFreshViewModel(nextPrincipal, credential)
        queryBalance()
        assertStoredSession(nextPrincipal, credential)
        evidence(id, GATEWAY)
    }

    private fun login(principal: String, waitForDevice: Boolean = true) {
        ui.waitUntil(20_000) { ui.onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().size == 1 }
        ui.onNodeWithTag(UiTags.LOGIN_USER).performScrollTo().performTextReplacement(principal)
        ui.onNodeWithTag(UiTags.LOGIN_PASSWORD).performScrollTo().performTextReplacement("Bank@2026")
        ui.submitLoginForm()
        ui.waitUntil(30_000) { model.state.value.authenticated && model.state.value.principal == principal }
        if (waitForDevice) ui.waitUntil(20_000) { !store.deviceId().isNullOrBlank() }
        ui.onNodeWithTag(UiTags.CHAT_INPUT).assertIsDisplayed()
    }

    private fun logout() {
        ui.openFromDrawer(UiTags.LOGOUT)
        ui.waitUntil(20_000) { !model.state.value.authenticated && !model.state.value.busy }
        ui.waitUntil(20_000) { ui.onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().size == 1 }
    }

    private fun queryBalance() {
        val previousIds = model.state.value.messages.map { it.id }.toSet()
        ui.sendChatMessage("查一下余额")
        ui.waitUntil(40_000) { !model.state.value.busy && model.state.value.messages.any { it.id !in previousIds && it.result != null } }
        assertNull(model.state.value.error)
        assertTrue(model.state.value.authenticated)
        val balanceReply = model.state.value.messages.last { it.id !in previousIds && it.result != null }
        val expectedBalance = if (model.state.value.principal == BOB) 2_000.0 else 15_000.0
        assertEquals("the response must contain the authenticated account's actual balance",
            setOf(expectedBalance), balanceValues(balanceReply.result).toSet())
    }

    private fun balanceValues(value: Any?): List<Double> = when (value) {
        is Map<*, *> -> value.flatMap { (key, nested) ->
            if (key == "total_balance" && nested is Number) listOf(nested.toDouble()) else balanceValues(nested)
        }
        is List<*> -> value.flatMap(::balanceValues)
        else -> emptyList()
    }

    private fun requestRedApproval(card: String) {
        ui.sendChatMessage("冻结卡片 $card")
        ui.waitUntil(40_000) { !model.state.value.busy && model.state.value.pending != null }
        assertTrue(checkNotNull(model.state.value.pending).mfaRequired)
        assertNull(model.state.value.error)
    }

    private fun enterDynamicOtp() {
        val label = "获取本机一次性演示验证码"
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasText(label))
        ui.onNodeWithText(label).performScrollTo().performClick()
        val matcher = hasText("本机演示验证码：", substring = true)
        ui.waitUntil(10_000) { ui.onAllNodes(matcher).fetchSemanticsNodes().isNotEmpty() }
        val text = ui.onNode(matcher).fetchSemanticsNode().config[androidx.compose.ui.semantics.SemanticsProperties.Text].joinToString("") { it.text }
        val code = checkNotNull(Regex("[0-9]{6}").find(text)).value
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_OTP))
        ui.onNodeWithTag(UiTags.APPROVAL_OTP).performScrollTo().performTextReplacement(code)
        assertEquals(code, model.state.value.otp)
    }

    private fun enterOtp() {
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_OTP))
        ui.onNodeWithTag(UiTags.APPROVAL_OTP).performScrollTo().performTextReplacement("888888")
        assertEquals("888888", model.state.value.otp)
    }

    private fun assertStoredSession(principal: String, credential: String) {
        val expectedIds = model.state.value.messages.map { it.id }
        ui.waitUntil(10_000) {
            store.messages()?.let { Gson().fromJson(it, Array<ChatMessage>::class.java).map { message -> message.id } } == expectedIds
        }
        assertEquals(principal, store.principal())
        assertEquals(credential, store.sessionToken())
        assertFalse(store.messages().orEmpty().contains("OLD_ACCOUNT_STAGE"))
        assertFalse(store.messages().orEmpty().contains("STALE_"))
    }

    private fun assertRestoresWithFreshViewModel(principal: String, credential: String) {
        val previousModel = model
        val expectedIds = model.state.value.messages.map { it.id }
        // Configuration recreation alone retains the ViewModel. Clear its store
        // first so this verifies the encrypted persistence restore path too.
        ui.activityRule.scenario.onActivity { it.viewModelStore.clear() }
        ui.activityRule.scenario.recreate()
        ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
        assertNotSame(previousModel, model)
        ui.waitUntil(20_000) { !model.state.value.restoring && model.state.value.authenticated }
        assertEquals(principal, model.state.value.principal)
        assertEquals(expectedIds, model.state.value.messages.map { it.id })
        assertEquals(credential, store.sessionToken())
        ui.onNodeWithTag(UiTags.CHAT_INPUT).assertIsDisplayed()
        ui.onNodeWithTag(UiTags.LOGIN_BUTTON).assertDoesNotExist()
    }

    /** Observe every synchronous StateFlow update, including transient errors. */
    private inner class StateProbe : AutoCloseable {
        private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        private val expected = stableState(model.state.value)
        private val observed = mutableListOf<ChatUiState>()
        init { scope.launch { model.state.collect { synchronized(observed) { observed += stableState(it) } } } }
        fun assertUnchanged() = synchronized(observed) {
            observed.forEach { assertEquals("stale work changed the new login", expected, it) }
        }
        override fun close() = scope.cancel()
    }

    private fun stableState(state: ChatUiState) = state.copy(notice = null)

    private fun assertStableFor(probe: StateProbe, millis: Long = 1_500) {
        val until = SystemClock.uptimeMillis() + millis
        do {
            ui.waitForIdle()
            probe.assertUnchanged()
            SystemClock.sleep(50)
        } while (SystemClock.uptimeMillis() < until)
        ui.onNodeWithTag(UiTags.LOGIN_BUTTON).assertDoesNotExist()
    }

    private fun hold(port: Int, id: String, path: String, method: String, mode: String, lateStage: String? = null) {
        val payload = JSONObject().put("id", id).put("path", path).put("method", method).put("principal", ALICE).put("mode", mode)
        if (lateStage != null) payload.put("late_stage", lateStage)
        control(port, "hold", payload)
        held += port to id
    }

    private fun waitHeld(port: Int, id: String) {
        ui.waitUntil(30_000) { gate(port, id).getBoolean("arrived") }
        val response = gate(port, id)
        assertEquals(200, response.getInt("original_status"))
        assertFalse(response.getBoolean("released"))
        assertFalse(response.getBoolean("completed"))
        if (response.getString("path") == STREAM_PATH) assertTrue(response.getInt("frames_sent") > 0)
    }

    private fun release(port: Int, id: String) {
        control(port, "release", JSONObject().put("id", id))
        ui.waitUntil(20_000) { gate(port, id).getBoolean("completed") }
        assertTrue(gate(port, id).getBoolean("released"))
        assertFalse(gate(port, id).getBoolean("timed_out"))
    }

    private fun gate(port: Int, id: String): JSONObject {
        val gates = control(port, "holds").getJSONArray("holds")
        return (0 until gates.length()).map { gates.getJSONObject(it) }.single { it.getString("id") == id }
    }

    private fun approvalRequests(): List<String> {
        val requests = control(GATEWAY, "requests").getJSONArray("requests")
        return (0 until requests.length()).map { requests.getJSONObject(it) }
            .filter { it.getString("path").startsWith("/api/v1/agent/approvals/") }
            .map { "${it.getString("method")} ${it.getString("path")} ${it.optString("principal")}" }
    }

    private fun evidence(name: String, port: Int) {
        val folder = File(instrumentation.targetContext.getExternalFilesDir(null), "account-isolation").apply { mkdirs() }
        val record = JSONObject().put("scenario", name).put("principal", model.state.value.principal)
            .put("authenticated", model.state.value.authenticated).put("message_count", model.state.value.messages.size)
            .put("holds", control(port, "holds").getJSONArray("holds"))
            .put("requests", control(GATEWAY, "requests").getJSONArray("requests"))
        File(folder, "$name.json").writeText(record.toString(2))
    }

    private fun control(port: Int, path: String, body: JSONObject? = null): JSONObject {
        val connection = URL("http://127.0.0.1:$port/__scenario/$path").openConnection() as HttpURLConnection
        connection.setRequestProperty("X-Scenario-Token", controlToken)
        connection.connectTimeout = 5_000
        connection.readTimeout = 15_000
        if (body != null) {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        return try { JSONObject(connection.inputStream.bufferedReader().use { it.readText() }) } finally { connection.disconnect() }
    }

    private fun authenticatedCore(path: String, body: JSONObject? = null): JSONObject {
        val connection = URL("http://127.0.0.1:$CORE/api/v1/$path").openConnection() as HttpURLConnection
        connection.setRequestProperty("X-User-Id", model.state.value.principal)
        connection.setRequestProperty("X-Session-Token", checkNotNull(store.sessionToken()))
        connection.connectTimeout = 5_000
        connection.readTimeout = 15_000
        if (body != null) {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        return try { JSONObject(connection.inputStream.bufferedReader().use { it.readText() }) } finally { connection.disconnect() }
    }

    private companion object {
        const val CORE = 18800
        const val GATEWAY = 18810
        const val ALICE = "user_zhang"
        const val BOB = "user_li"
        const val STREAM_PATH = "/api/v1/agent/messages/stream"
    }
}
