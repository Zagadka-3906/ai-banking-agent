package com.aibanking.agent

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.ChatMessage
import com.aibanking.agent.ui.chat.ChatViewModel
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.net.HttpURLConnection
import java.net.URL

/** Explicit live-model acceptance; not part of the credential-free offline suite. */
class FocusedAnswerUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()
    private val token get() = checkNotNull(InstrumentationRegistry.getArguments().getString("controlToken"))
    private lateinit var model: ChatViewModel

    @Test fun focused_answers_remain_visible_and_side_questions_keep_the_original_approval() {
        assumeTrue(BuildConfig.APPLICATION_ID.endsWith(".scenariotest"))
        ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
        ui.waitUntil(20_000) { !model.state.value.restoring }
        if (model.state.value.authenticated) ui.runOnIdle { model.logout() }
        ui.waitUntil(20_000) { !model.state.value.authenticated && !model.state.value.busy }
        assertEquals("live", control(18810, "reset", JSONObject()).getString("mode"))
        control(18800, "reset", JSONObject())
        ui.ensureLoggedIn("Bank@2026")
        val before = control(18800, "state").toString()
        val balanceTitles = ui.onAllNodesWithText("账户余额").fetchSemanticsNodes().size
        val balance = turn("只告诉我现在能用多少钱")
        assertTrue(balance.text.contains("可用余额"))
        assertFalse(balance.text.contains("总余额"))
        assertTrue(balance.result?.get("answer_presentation") is Map<*, *>)
        ui.onNodeWithText("已查询账户余额：").assertDoesNotExist()
        ui.onAllNodesWithText("账户余额").assertCountEquals(balanceTitles)
        ui.onAllNodesWithText("可用余额", substring = true).onLast().assertExists()

        turn("给李四转300元")
        val handle = checkNotNull(model.state.value.pending).approvalId
        val explanation = turn("这次还要验证码吗")
        assertTrue(explanation.text.contains("无需验证码"))
        assertFalse(explanation.approvalPreview)
        assertEquals(handle, model.state.value.pending?.approvalId)
        val check = turn("先查下余额")
        assertTrue(check.text.contains("可用余额"))
        assertFalse(check.approvalPreview)
        assertEquals(handle, model.state.value.pending?.approvalId)
        assertEquals(before, control(18800, "state").toString())
        ui.runOnIdle { model.reject() }
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending == null }
        assertTrue(model.state.value.messages.any { it.id == explanation.id && it.text.contains("无需验证码") })
        assertEquals(before, control(18800, "state").toString())
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).assertDoesNotExist()
        val calls = control(18810, "records").getJSONArray("model_calls")
        assertTrue(calls.length() >= 7)
        for (index in 0 until calls.length()) assertTrue(calls.getJSONObject(index).getBoolean("success"))
    }

    private fun turn(text: String): ChatMessage {
        val prior = model.state.value.messages.map { it.id }.toSet()
        ui.sendChatMessage(text)
        ui.waitUntil(60_000) {
            !model.state.value.busy && model.state.value.messages.any { it.id !in prior && it.businessStatus != null }
        }
        return model.state.value.messages.last { it.id !in prior && it.businessStatus != null }
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
