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
        assertTrue(noOtp(explanation.text))
        assertFalse(explanation.approvalPreview)
        assertEquals(handle, model.state.value.pending?.approvalId)
        val check = turn("先查下余额")
        assertTrue(check.text.contains("可用余额"))
        assertFalse(check.approvalPreview)
        assertEquals(handle, model.state.value.pending?.approvalId)
        assertEquals(before, control(18800, "state").toString())
        ui.runOnIdle { model.reject() }
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending == null }
        assertTrue(model.state.value.messages.any { it.id == explanation.id && noOtp(it.text) })
        assertEquals(before, control(18800, "state").toString())
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).assertDoesNotExist()
        val procedure = turn("解冻需要去柜台办理吗")
        assertTrue(procedure.text.contains("App", ignoreCase = true))
        assertTrue(Regex("不需要|无需|不用").containsMatchIn(procedure.text))
        assertTrue(procedure.text.contains("验证码") && procedure.text.contains("签名"))
        assertNull(model.state.value.pending)
        val crossScene = turn("跨场景联动怎么实现")
        assertTrue(listOf("生日", "提醒", "草稿").all { crossScene.text.contains(it) })
        assertNull(model.state.value.pending)
        assertEquals(before, control(18800, "state").toString())
        val calls = control(18810, "records").getJSONArray("model_calls")
        assertTrue(calls.length() >= 7)
        for (index in 0 until calls.length()) assertTrue(calls.getJSONObject(index).getBoolean("success"))
    }

    private fun noOtp(text: String) = Regex("(?:无需|不需要|不用)[^。；\\n]{0,12}验证码").containsMatchIn(text)

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
