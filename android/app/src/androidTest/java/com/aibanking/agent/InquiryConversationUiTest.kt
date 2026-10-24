package com.aibanking.agent

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.ChatViewModel
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import java.net.HttpURLConnection
import java.net.URL

/** The reported inquiry sequence through real Compose, SSE and isolated Core. */
@BankingRegression
class InquiryConversationUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val token get() = checkNotNull(InstrumentationRegistry.getArguments().getString("controlToken"))
    private lateinit var model: ChatViewModel
    private var accountLabelCount = 0

    @Before fun reset() {
        assumeTrue(BuildConfig.APPLICATION_ID.endsWith(".scenariotest"))
        ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
        ui.waitUntil(20_000) { !model.state.value.restoring }
        if (model.state.value.authenticated) ui.runOnIdle { model.logout() }
        ui.waitUntil(20_000) { !model.state.value.authenticated && !model.state.value.busy }
        control(18810, "reset", JSONObject())
        control(18800, "reset", JSONObject())
        ui.ensureLoggedIn("Bank@2026")
        accountLabelCount = ui.onAllNodesWithText("user_zhang").fetchSemanticsNodes().size
    }

    @Test fun suspicious_transaction_and_dispute_followups_stay_in_the_dialogue_without_writes() {
        val before = control(18800, "state").toString()
        turn("分析本月账单", "BILL_ANALYSIS")
        turn("那6笔可疑交易帮我查下", "BILL_QUERY")
        turn("我的卡被盗刷了吗", "BILL_QUERY")
        turn("这是盗刷吗", "BILL_QUERY")
        turn("争议工单进度怎么查", "DISPUTE_QUERY")
        turn("争议工单多少有结果", "DISPUTE_QUERY")
        assertTrue(model.state.value.messages.last().text.contains("预计完成时间"))
        ui.onAllNodesWithText("预计完成时间", substring = true).onLast().assertExists()
        assertEquals(before, control(18800, "state").toString())
    }

    private fun turn(text: String, expected: String) {
        val previous = model.state.value.messages.map { it.id }.toSet()
        ui.sendChatMessage(text)
        ui.waitUntil(30_000) {
            !model.state.value.busy && model.state.value.messages.any { it.id !in previous && it.businessStatus != null }
        }
        val reply = model.state.value.messages.last { it.id !in previous && it.businessStatus != null }
        assertEquals("SUCCEEDED", reply.businessStatus)
        assertEquals(listOf(expected), reply.response?.sections?.map { it.type })
        assertNull(model.state.value.pending)
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).assertDoesNotExist()
        ui.onNodeWithTag(UiTags.OUTCOME_SCREEN).assertDoesNotExist()
        assertFalse(reply.text.contains("user_zhang"))
        // The account drawer already contains the login name; new replies must
        // not add a raw owner field in their result cards.
        ui.onAllNodesWithText("user_zhang").assertCountEquals(accountLabelCount)
    }

    private fun control(port: Int, path: String, body: JSONObject? = null): JSONObject {
        val connection = URL("http://127.0.0.1:$port/__scenario/$path").openConnection() as HttpURLConnection
        connection.setRequestProperty("X-Scenario-Token", token)
        connection.connectTimeout = 10_000
        connection.readTimeout = 20_000
        if (body != null) {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        return try { JSONObject(connection.inputStream.bufferedReader().use { it.readText() }) } finally { connection.disconnect() }
    }
}
