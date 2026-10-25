package com.aibanking.agent

import android.os.SystemClock
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.*
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.data.AgentApi
import com.aibanking.agent.data.AgentRepository
import com.aibanking.agent.data.SessionStore
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.ChatMessage
import com.aibanking.agent.ui.chat.ChatUiState
import com.aibanking.agent.ui.chat.ChatViewModel
import com.aibanking.agent.ui.chat.isTaskOutcome
import com.google.gson.JsonSyntaxException
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.lang.reflect.Proxy

/**
 * Real Compose navigation, ViewModel, Core receipts and gateway HTTP responses.
 *
 * The reconciliation fixture first completes a real approved transfer, then
 * verifies its plan's operation id against the authenticated Core receipt, and
 * asks the test runner to retain that operation's reconciliation metadata. Only
 * the Android message is then marked as awaiting reconciliation. The loopback
 * runner holds or rejects the real gateway reconciliation response; no operation
 * id or settlement is invented.
 * The parsing-fault case alone swaps the gateway API for a Java proxy that
 * throws a decoding exception inside the real AgentRepository. All other cases
 * use real repositories and HTTP responses. No system clock is changed.
 */
@BankingRegression
class ScenarioOutcomeUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val controlToken get() = checkNotNull(InstrumentationRegistry.getArguments().getString("controlToken"))
    private lateinit var model: ChatViewModel
    private val held = mutableListOf<String>()

    @Before fun reset() {
        assumeTrue(BuildConfig.APPLICATION_ID.endsWith(".scenariotest"))
        ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
        ui.waitUntil(20_000) { !model.state.value.restoring }
        if (model.state.value.authenticated) ui.runOnIdle { model.logout() }
        ui.waitUntil(20_000) { !model.state.value.authenticated && !model.state.value.busy }
        control(GATEWAY, "reset", JSONObject())
        control(CORE, "reset", JSONObject())
        login(ALICE)
    }

    @After fun releaseOutstandingResponses() {
        held.forEach { id -> runCatching { control(GATEWAY, "release", JSONObject().put("id", id)) } }
    }

    @Test fun old_result_navigation_and_recreation_do_not_extend_the_pending_approval() {
        val balance = queryBalance()
        requestTransfer(10)
        val approval = checkNotNull(model.state.value.pending)
        val beforeSeconds = approvalSeconds()
        val beforeTime = SystemClock.elapsedRealtime()

        openOutcome(balance.id)
        ui.onNodeWithTag(UiTags.OUTCOME_BALANCE).performScrollTo().assertIsNotEnabled()
        SystemClock.sleep(3_000)
        ui.activityRule.scenario.recreate()
        ui.waitUntil(10_000) { ui.onAllNodesWithTag(UiTags.OUTCOME_SCREEN).fetchSemanticsNodes().size == 1 }
        ui.onNodeWithTag(UiTags.OUTCOME_BACK).performClick()

        val afterSeconds = approvalSeconds()
        val elapsedSeconds = (SystemClock.elapsedRealtime() - beforeTime) / 1_000L
        assertEquals(approval, model.state.value.pending)
        assertTrue("approval deadline must continue while the result is open and across recreation",
            afterSeconds <= beforeSeconds - elapsedSeconds + 1)
        assertTrue("the test must actually spend time away from the approval", elapsedSeconds >= 3)
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).assertIsEnabled()
    }

    @Test fun a_stale_balance_callback_cannot_replace_a_pending_approval_or_close_its_result() {
        val balance = queryBalance()
        requestTransfer(10)
        openOutcome(balance.id)
        ui.onNodeWithTag(UiTags.OUTCOME_BALANCE).performScrollTo().assertIsNotEnabled()
        val before = model.state.value
        val requestsBefore = requestCount(GATEWAY, STREAM_PATH)

        // Exercise a callback captured while the button was previously enabled.
        ui.runOnIdle { model.outcomeBalance() }
        SystemClock.sleep(500)
        ui.waitForIdle()
        val after = model.state.value
        assertEquals(before.pending, after.pending)
        assertEquals(before.messages, after.messages)
        assertEquals(before.input, after.input)
        assertEquals(before.outcomeMessageId, after.outcomeMessageId)
        assertEquals(requestsBefore, requestCount(GATEWAY, STREAM_PATH))
        ui.onNodeWithTag(UiTags.OUTCOME_SCREEN).assertIsDisplayed()
    }

    @Test fun a_delayed_receipt_cannot_remove_a_newer_approval() {
        val receipt = pendingLocalReceipt()
        hold("old-receipt", reconciliationPath(receipt))
        ui.runOnIdle { model.reconcileMessage(receipt.id) }
        waitHeld("old-receipt")

        requestTransfer(20)
        val current = model.state.value
        val approval = checkNotNull(current.pending)
        val previewIds = current.messages.filter { it.approvalPreview }.map { it.id }
        assertTrue(previewIds.isNotEmpty())
        release("old-receipt")
        ui.waitUntil(15_000) { message(receipt.id).businessStatus == "SUCCEEDED" }

        assertEquals(approval, model.state.value.pending)
        assertEquals(previewIds, model.state.value.messages.filter { it.approvalPreview }.map { it.id })
        assertEquals(current.quiz, model.state.value.quiz)
        assertEquals(current.busy, model.state.value.busy)
        assertEquals(current.otp, model.state.value.otp)
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_APPROVE))
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).assertIsEnabled()
        assertEquals("reconciliation must not debit the original transfer twice", 1,
            requestCount(CORE, "/api/v1/transfers/execute"))
    }

    @Test fun a_delayed_receipt_cannot_clear_a_newer_request_in_progress() {
        val receipt = pendingLocalReceipt()
        hold("old-receipt-busy", reconciliationPath(receipt))
        ui.runOnIdle { model.reconcileMessage(receipt.id) }
        waitHeld("old-receipt-busy")
        hold("new-turn", STREAM_PATH, method = "POST")
        ui.sendChatMessage("给李四转20元")
        waitHeld("new-turn")
        assertTrue(model.state.value.busy)
        val currentIds = model.state.value.messages.map { it.id }

        release("old-receipt-busy")
        ui.waitUntil(15_000) { message(receipt.id).businessStatus == "SUCCEEDED" }
        assertTrue("old reconciliation cannot enable sending during a newer stream", model.state.value.busy)
        assertEquals(currentIds, model.state.value.messages.map { it.id })
        release("new-turn")
        ui.waitUntil(20_000) { !model.state.value.busy && model.state.value.pending != null }
        assertTrue(checkNotNull(model.state.value.pending).summary.contains("20"))
    }

    @Test fun reconciliation_failure_is_visible_on_the_result_and_retry_only_reads_the_receipt() {
        val receipt = pendingLocalReceipt()
        hold("receipt-error", reconciliationPath(receipt), mode = "http_500")
        openOutcome(receipt.id)
        ui.onNodeWithTag(UiTags.OUTCOME_RECONCILE).performScrollTo().performClick()
        waitHeld("receipt-error")
        release("receipt-error")
        ui.waitUntil(15_000) { !message(receipt.id).reconciling && message(receipt.id).reconciliationError != null }

        val failure = checkNotNull(message(receipt.id).reconciliationError)
        assertEquals("PENDING_RECONCILIATION", message(receipt.id).businessStatus)
        assertEquals(receipt.id, model.state.value.outcomeMessageId)
        ui.onNodeWithText(failure, substring = true).performScrollTo().assertIsDisplayed()
        ui.onNodeWithTag(UiTags.OUTCOME_RECONCILE).performScrollTo().assertIsEnabled().performClick()
        ui.waitUntil(20_000) { message(receipt.id).businessStatus == "SUCCEEDED" && !message(receipt.id).reconciling }
        assertNull(message(receipt.id).reconciliationError)
        assertEquals(receipt.id, model.state.value.outcomeMessageId)
        ui.onNodeWithTag(UiTags.OUTCOME_TITLE).performScrollTo().assertTextEquals("转账成功")
        assertEquals(1, requestCount(CORE, "/api/v1/transfers/execute"))
        assertEquals(2, requestCount(GATEWAY, reconciliationPath(receipt)))
    }

    @Test fun result_actions_return_to_chat_and_logout_clears_the_previous_accounts_result() {
        val first = queryBalance()
        openOutcome(first.id)
        ui.runOnIdle {
            model.onInputChange("尚未发送的下一条草稿")
            stateFlow().value = model.state.value.copy(suggestions = emptyList())
        }
        val previousIds = model.state.value.messages.map { it.id }.toSet()
        ui.onNodeWithTag(UiTags.OUTCOME_BALANCE).performScrollTo().performClick()
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.messages.any {
            it.id !in previousIds && it.businessStatus == "SUCCEEDED"
        } }
        assertNull(model.state.value.outcomeMessageId)
        assertEquals("尚未发送的下一条草稿", model.state.value.input)
        ui.onNodeWithTag(UiTags.CHAT_INPUT).assertIsDisplayed()

        val latest = model.state.value.messages.last { it.businessStatus == "SUCCEEDED" }
        openOutcome(latest.id)
        val taskAction = ui.onNodeWithTag(UiTags.OUTCOME_TASKS).performScrollTo().assertIsEnabled()
        // Synchronize with the drawn frame after scrolling, and retain it for visual review.
        captureOutcomeScreen("task-entry")
        taskAction.performClick()
        captureOutcomeScreen("task-panel")
        ui.runOnIdle { assertTrue("The task shortcut must open its panel", model.state.value.showTasks) }
        ui.waitUntil(15_000) { model.state.value.showTasks && !model.state.value.panelBusy }
        assertNull(model.state.value.outcomeMessageId)
        assertNull(model.state.value.panelError)
        ui.runOnIdle { ui.activity.onBackPressedDispatcher.onBackPressed() }
        ui.waitUntil(10_000) { !model.state.value.showTasks }
        openOutcome(latest.id)

        // Logout invalidation is also required while the full-screen result is active.
        ui.runOnIdle { model.logout() }
        login(BOB)
        assertNull(model.state.value.outcomeMessageId)
        assertNull(model.state.value.lastProblem)
        assertFalse(model.state.value.showProblem)
        assertTrue(model.state.value.messages.none { it.id in previousIds || it.id == latest.id })
        ui.onNodeWithTag(UiTags.OUTCOME_SCREEN).assertDoesNotExist()
        ui.onNodeWithTag(UiTags.CHAT_INPUT).assertIsDisplayed()
    }

    @Test fun an_approval_decoding_failure_keeps_the_approval_and_explains_the_unknown_result() {
        requestTransfer(10)
        val approval = checkNotNull(model.state.value.pending)
        val agentField = ChatViewModel::class.java.getDeclaredField("agent").apply { isAccessible = true }
        val original = agentField.get(model)
        val failingApi = Proxy.newProxyInstance(AgentApi::class.java.classLoader,
            arrayOf(AgentApi::class.java)) { proxy, method, args ->
            when (method.name) {
                "resolveApprovalStream" -> throw JsonSyntaxException("SCENARIO_APPROVAL_DECODING_FAILURE")
                "toString" -> "ScenarioParsingFailureApi"
                "hashCode" -> System.identityHashCode(proxy)
                "equals" -> proxy === args?.firstOrNull()
                else -> error("Unexpected API call in parsing fixture: ${method.name}")
            }
        } as AgentApi
        try {
            ui.runOnIdle { agentField.set(model, AgentRepository(failingApi)) }
            ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_APPROVE))
            ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performClick()
            ui.waitUntil(15_000) { !model.state.value.busy && model.state.value.lastProblem != null }

            assertEquals(approval, model.state.value.pending)
            assertTrue(model.state.value.authenticated)
            val problem = checkNotNull(model.state.value.lastProblem)
            assertEquals("CLIENT_ERROR", problem.code)
            assertEquals(0, problem.httpStatus)
            ui.onNodeWithTag(UiTags.OPEN_FAILURE).performClick()
            ui.onNodeWithTag(UiTags.OUTCOME_TITLE).assertTextEquals("暂未取得办理结果")
            ui.onNodeWithText("结果待核对").assertIsDisplayed()
            ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).assertDoesNotExist()
            ui.onNodeWithTag(UiTags.OUTCOME_RECONCILE).assertDoesNotExist()
            ui.onNodeWithTag(UiTags.OUTCOME_BALANCE).assertDoesNotExist()
            ui.onAllNodes(hasClickAction() and hasText("重新", substring = true)).assertCountEquals(0)
            ui.onNodeWithTag(UiTags.OUTCOME_RETURN).assertIsEnabled()
        } finally {
            ui.runOnIdle { agentField.set(model, original) }
        }
    }

    private fun login(principal: String) {
        ui.waitUntil(20_000) { ui.onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().size == 1 }
        ui.onNodeWithTag(UiTags.LOGIN_USER).performScrollTo().performTextReplacement(principal)
        ui.onNodeWithTag(UiTags.LOGIN_PASSWORD).performScrollTo().performTextReplacement("Bank@2026")
        ui.submitLoginForm()
        ui.waitUntil(30_000) { model.state.value.authenticated && model.state.value.principal == principal }
        ui.onNodeWithTag(UiTags.CHAT_INPUT).assertIsDisplayed()
    }

    private fun queryBalance(): ChatMessage {
        val previousIds = model.state.value.messages.map { it.id }.toSet()
        ui.sendChatMessage("查一下余额")
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.messages.any {
            it.id !in previousIds && it.businessStatus == "SUCCEEDED"
        } }
        return model.state.value.messages.last { it.id !in previousIds && it.businessStatus == "SUCCEEDED" }
    }

    private fun requestTransfer(amount: Int) {
        ui.sendChatMessage("给李四转${amount}元")
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending != null }
        assertFalse("small transfers must use real yellow approval", checkNotNull(model.state.value.pending).mfaRequired)
    }

    private fun pendingLocalReceipt(): ChatMessage {
        requestTransfer(10)
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_APPROVE))
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performClick()
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending == null &&
            model.state.value.messages.any { it.businessStatus == "SUCCEEDED" } }
        val receipt = model.state.value.messages.last { it.businessStatus == "SUCCEEDED" }
        checkNotNull(receipt.conversationId)
        // Successful graph turns omit reconciliation metadata. Their public plan
        // still contains the exact idempotency key used for the real Core debit.
        val plan = receipt.result?.get("plan") as? Map<*, *> ?: error("completed transfer must expose its plan")
        val transferTask = (plan["tasks"] as List<*>).filterIsInstance<Map<*, *>>().single { it["action"] == "TRANSFER" }
        val operation = transferTask["idempotency_key"] as String
        val coreReceipt = authenticatedCore("operations/${URLEncoder.encode(operation, "UTF-8")}?endpoint=transfer_execute")
        assertEquals("COMPLETED", coreReceipt.getString("status"))
        assertEquals(operation, coreReceipt.getString("operation_id"))
        val completedData = checkNotNull(receipt.response).sections.single { it.type == "TRANSFER_SUCCESS" }.data["data"] as Map<*, *>
        assertEquals(completedData["transaction_id"], coreReceipt.getJSONObject("receipt").getString("transaction_id"))
        val retained = control(GATEWAY, "retain-receipt", JSONObject().put("principal", ALICE)
            .put("conversation_id", receipt.conversationId).put("operation_id", operation))
        assertEquals(operation, retained.getString("operation_id"))
        val pendingText = "本次操作暂未取得回执，请先核对结果。"
        val pending = receipt.copy(businessStatus = "PENDING_RECONCILIATION", reconciling = false,
            text = pendingText, result = null, response = checkNotNull(receipt.response).copy(
                status = "PENDING_RECONCILIATION", summary = pendingText, sections = emptyList(), operationId = operation))
        // Test-only fixture: retain the actual operation identity and real server receipt.
        ui.runOnIdle {
            stateFlow().value = model.state.value.copy(messages = model.state.value.messages.map {
                if (it.id == receipt.id) pending else it
            })
        }
        return pending
    }

    @Suppress("UNCHECKED_CAST")
    private fun stateFlow(): MutableStateFlow<ChatUiState> {
        val field = ChatViewModel::class.java.getDeclaredField("_state").apply { isAccessible = true }
        return field.get(model) as MutableStateFlow<ChatUiState>
    }

    private fun approvalSeconds(): Int {
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_APPROVE))
        val text = ui.onNode(hasText("请在", substring = true)).fetchSemanticsNode()
            .config.getOrNull(SemanticsProperties.Text).orEmpty().joinToString { it.text }
        return checkNotNull(Regex("请在 (\\d+) 秒内确认").find(text)).groupValues[1].toInt()
    }

    private fun openOutcome(id: String) {
        // Approved operations already open their own result page.
        if (model.state.value.outcomeMessageId == id && ui.onAllNodesWithTag(UiTags.OUTCOME_SCREEN).fetchSemanticsNodes().isNotEmpty()) return
        ui.returnFromOutcome(settleMs = 500)
        // Queries are answered in the conversation without a result button.
        if (!isTaskOutcome(message(id))) {
            ui.runOnIdle { model.openOutcome(id) }
            ui.onNodeWithTag(UiTags.OUTCOME_SCREEN).assertIsDisplayed()
            return
        }
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.outcomeEntry(id)))
        ui.onNodeWithTag(UiTags.outcomeEntry(id)).performClick()
        ui.onNodeWithTag(UiTags.OUTCOME_SCREEN).assertIsDisplayed()
    }

    private fun message(id: String) = model.state.value.messages.single { it.id == id }

    private fun captureOutcomeScreen(name: String) {
        val extra = InstrumentationRegistry.getArguments().getString("additionalTestOutputDir")
        val folder = (if (extra != null) java.io.File(extra, "outcome-navigation") else
            java.io.File(instrumentation.targetContext.getExternalFilesDir(null), "outcome-navigation")).apply { mkdirs() }
        val bitmap = ui.onRoot().captureToImage().asAndroidBitmap()
        try {
            java.io.File(folder, "$name.png").outputStream().use {
                check(bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it))
            }
        } finally { bitmap.recycle() }
    }
    private fun reconciliationPath(message: ChatMessage) =
        "/api/v1/agent/conversations/${checkNotNull(message.conversationId)}/operations/${checkNotNull(message.response?.operationId)}"

    private fun hold(id: String, path: String, method: String = "GET", mode: String = "success") {
        control(GATEWAY, "hold", JSONObject().put("id", id).put("path", path)
            .put("method", method).put("principal", ALICE).put("mode", mode))
        held += id
    }

    private fun waitHeld(id: String) {
        ui.waitUntil(30_000) { gate(id).getBoolean("arrived") }
        val gate = gate(id)
        assertEquals(200, gate.getInt("original_status"))
        assertTrue(gate.isNull("failure"))
        assertFalse(gate.getBoolean("completed"))
    }

    private fun release(id: String) {
        control(GATEWAY, "release", JSONObject().put("id", id))
        ui.waitUntil(15_000) { gate(id).getBoolean("completed") }
        assertFalse(gate(id).getBoolean("timed_out"))
    }

    private fun gate(id: String): JSONObject {
        val gates = control(GATEWAY, "holds").getJSONArray("holds")
        return (0 until gates.length()).map { gates.getJSONObject(it) }.single { it.getString("id") == id }
    }

    private fun requestCount(port: Int, path: String): Int {
        val requests = control(port, "requests").getJSONArray("requests")
        return (0 until requests.length()).count { requests.getJSONObject(it).getString("path") == path }
    }

    private fun authenticatedCore(path: String): JSONObject {
        val store = SessionStore(instrumentation.targetContext)
        val connection = URL("http://127.0.0.1:$CORE/api/v1/$path").openConnection() as HttpURLConnection
        connection.setRequestProperty("X-User-Id", model.state.value.principal)
        connection.setRequestProperty("X-Session-Token", checkNotNull(store.sessionToken()))
        connection.connectTimeout = 5_000
        connection.readTimeout = 15_000
        return try { JSONObject(connection.inputStream.bufferedReader().use { it.readText() }) } finally { connection.disconnect() }
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

    private companion object {
        const val CORE = 18800
        const val GATEWAY = 18810
        const val ALICE = "user_zhang"
        const val BOB = "user_li"
        const val STREAM_PATH = "/api/v1/agent/messages/stream"
    }
}
