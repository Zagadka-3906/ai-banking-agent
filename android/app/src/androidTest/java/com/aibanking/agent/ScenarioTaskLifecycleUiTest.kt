package com.aibanking.agent

import android.graphics.Bitmap
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.ChatViewModel
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/** Real chat approvals and task-panel buttons, with isolated scheduler time. */
@BankingRegression
class ScenarioTaskLifecycleUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val token get() = checkNotNull(InstrumentationRegistry.getArguments().getString("controlToken"))
    private lateinit var model: ChatViewModel
    private val folder get() = File(instrumentation.targetContext.getExternalFilesDir(null), "task-lifecycle").apply { mkdirs() }

    @Before fun reset() {
        assumeTrue(BuildConfig.APPLICATION_ID.endsWith(".scenariotest"))
        ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
        ui.waitUntil(20_000) { ui.onAllNodesWithTag(UiTags.LOGOUT).fetchSemanticsNodes().isNotEmpty() ||
            ui.onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().isNotEmpty() }
        if (ui.onAllNodesWithTag(UiTags.LOGOUT).fetchSemanticsNodes().isNotEmpty()) ui.openFromDrawer(UiTags.LOGOUT)
        ui.waitUntil(20_000) { ui.onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().isNotEmpty() }
        control(18810, "reset", JSONObject())
        control(18800, "reset", JSONObject())
        ui.ensureLoggedIn("Bank@2026")
    }

    @Test fun scheduled_transfer_due_waits_then_cancels_without_debit() {
        create("明天下午三点给李四转50元")
        assertEquals("SCHEDULED", control(18800, "state").getJSONArray("scheduled_transfers").getJSONObject(0).getString("status"))
        control(18800, "advance-due", JSONObject())
        openTasks()
        assertEquals("WAITING_USER", model.state.value.tasks.single().status)
        ui.onNodeWithText("待您确认").assertIsDisplayed()
        cancelFirst()
        val state = evidence("schedule-cancelled")
        assertEquals("CANCELLED", state.getJSONArray("scheduled_transfers").getJSONObject(0).getString("status"))
        assertEquals(15000.0, state.getDouble("balance"), 0.001)
        assertEquals(9, state.getJSONArray("transactions").length())
        closeAndCheckChat()
    }

    @Test fun aa_request_can_be_cancelled_from_the_panel_without_collecting_money() {
        create("和李四、王美AA分摊100元")
        openTasks()
        assertEquals("SPLIT_BILL", model.state.value.tasks.single().taskType)
        cancelFirst()
        val state = evidence("aa-cancelled")
        val split = state.getJSONArray("splits").getJSONObject(0)
        assertEquals("CANCELLED", split.getString("status"))
        assertEquals(0.0, split.getDouble("collected_amount"), 0.001)
        assertEquals(15000.0, state.getDouble("balance"), 0.001)
        closeAndCheckChat()
    }

    @Test fun birthday_due_delivers_reminder_and_leaves_two_orders_waiting_for_user() {
        create("我爱人生日是2099-10-03，锁定1000元提醒订鲜花和蛋糕")
        control(18800, "advance-due", JSONObject())
        openTasks()
        val tasks = model.state.value.tasks
        assertEquals(4, tasks.size)
        assertEquals("SUCCEEDED", tasks.single { it.taskType == "REMINDER" }.status)
        assertEquals(2, tasks.count { it.taskType == "ORDER_DRAFT" && it.status == "WAITING_USER" })
        val state = evidence("birthday-due")
        assertEquals(15000.0, state.getDouble("balance"), 0.001)
        assertEquals(1000.0, state.getDouble("locked_amount"), 0.001)
        assertEquals(9, state.getJSONArray("transactions").length())
        closeAndCheckChat()
    }

    @Test fun risk_questionnaire_buttons_keep_progress_after_activity_recreation() {
        ui.sendChatMessage("我想做风险测评")
        ui.waitUntil(100_000) { !model.state.value.busy && model.state.value.quiz != null }
        repeat(8) { index ->
            val question = checkNotNull(model.state.value.quiz?.currentQuestion)
            assertEquals(index + 1, question.number)
            val label = question.options.single { it.key == "B" }.label
            val option = hasText(label, substring = true) and hasClickAction()
            ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(option)
            ui.onNode(option).performClick()
            if (index < 7) {
                ui.waitUntil(10_000) { model.state.value.quiz?.answered == index + 1 }
                if (index == 2) {
                    ui.activityRule.scenario.recreate()
                    ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
                    ui.waitUntil(20_000) { model.state.value.quiz?.answered == 3 }
                }
            }
        }
        ui.waitUntil(100_000) { !model.state.value.busy && model.state.value.quiz == null && model.state.value.profileResult != null }
        assertEquals(null, model.state.value.error)
        val state = evidence("quiz-completed")
        assertEquals(15000.0, state.getDouble("balance"), 0.001)
        File(folder, "quiz-profile.json").writeText(JSONObject(checkNotNull(model.state.value.profileResult)).toString(2))
    }

    @Test fun scheduled_task_can_pause_for_manual_review_from_the_real_panel() {
        create("明天下午三点给李四转50元")
        openTasks()
        ui.onNodeWithTag("task_takeover").performScrollTo().performClick()
        ui.onNodeWithText("处理原因").performTextInput("需要人工核对日期")
        ui.onNodeWithText("确认暂停").performClick()
        ui.waitUntil(20_000) { !model.state.value.panelBusy && model.state.value.tasks.single().status == "HUMAN_TAKEOVER" }
        assertEquals("需要人工核对日期", model.state.value.tasks.single().error)
        val state = evidence("manual-review")
        assertEquals(15000.0, state.getDouble("balance"), 0.001)
        control(18800, "advance-due", JSONObject())
        assertEquals("HUMAN_TAKEOVER", control(18800, "state").getJSONArray("tasks").getJSONObject(0).getString("status"))
    }

    private fun create(text: String) {
        ui.sendChatMessage(text)
        ui.waitUntil(100_000) { !model.state.value.busy && model.state.value.pending != null }
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_APPROVE))
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().performClick()
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending == null }
        assertEquals(null, model.state.value.error)
        assertTrue(control(18800, "state").getJSONArray("tasks").length() > 0)
    }

    private fun openTasks() {
        ui.openFromDrawer(UiTags.OPEN_TASKS)
        ui.waitUntil(20_000) { model.state.value.showTasks && !model.state.value.panelBusy && model.state.value.tasks.isNotEmpty() }
        ui.onNodeWithText("待办任务").assertIsDisplayed()
    }

    private fun cancelFirst() {
        ui.onAllNodesWithTag(UiTags.TASK_CANCEL).onFirst().performScrollTo().performClick()
        ui.waitUntil(20_000) { !model.state.value.panelBusy && model.state.value.tasks.any { it.status == "CANCELLED" } }
        assertEquals(null, model.state.value.error)
    }

    private fun closeAndCheckChat() {
        instrumentation.uiAutomation.executeShellCommand("input keyevent 4").close()
        ui.waitUntil(10_000) { !model.state.value.showTasks }
        ui.onNodeWithTag(UiTags.CHAT_INPUT).assertIsDisplayed()
    }

    private fun evidence(name: String): JSONObject {
        val state = control(18800, "state")
        File(folder, "$name.json").writeText(state.toString(2))
        File(folder, "$name.png").outputStream().use {
            ui.onAllNodes(isRoot()).onLast().captureToImage().asAndroidBitmap().compress(Bitmap.CompressFormat.PNG, 100, it)
        }
        return state
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
