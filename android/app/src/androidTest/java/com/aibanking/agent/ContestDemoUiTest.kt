package com.aibanking.agent

import android.graphics.Bitmap
import android.os.SystemClock
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.ChatViewModel
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/** Real UI story for screen recording, deliberately separate from the regression annotation. */
class ContestDemoUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val token get() = checkNotNull(InstrumentationRegistry.getArguments().getString("controlToken"))
    private lateinit var model: ChatViewModel
    private val folder get() = File(instrumentation.targetContext.getExternalFilesDir(null), "contest-demo").apply { mkdirs() }
    private val chapters = JSONArray()

    @Test fun record_real_query_confirmation_and_manual_handoff() {
        org.junit.Assume.assumeTrue(BuildConfig.APPLICATION_ID.endsWith(".scenariotest") &&
            InstrumentationRegistry.getArguments().getString("recordContestDemo") == "true")
        ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
        ui.waitUntil(20_000) { !model.state.value.restoring }
        if (model.state.value.authenticated) {
            ui.runOnIdle { model.dismissPanels(); model.logout() }
            ui.waitUntil(20_000) { !model.state.value.authenticated && !model.state.value.busy }
        }
        control(18810, "reset")
        control(18800, "reset")
        ui.ensureLoggedIn("Bank@2026")
        mark("安言：银行智能体", "真实 Android 应用、本机 Mock Core；全部为虚构账户。本片使用规则模式。")
        query("查一下余额")
        hold("绿色查询", "无需二次确认，余额来自 Core 账本。", "balance")
        query("分析本月餐饮支出")
        hold("账单分析", "按日期和类别核对消费事实。", "bills")
        ui.sendChatMessage("给李四转300元")
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending != null }
        assertFalse(checkNotNull(model.state.value.pending).mfaRequired)
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_APPROVE))
        hold("黄色操作：等待确认", "收款人、金额展示后，由用户明确确认。", "approval")
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().performClick()
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending == null }
        assertEquals(14700.0, control(18800, "state").getDouble("balance"), .001)
        hold("核验执行回执", "只有 Core 完成记账后才展示成功；余额实际减少 300 元。", "receipt")
        ui.sendChatMessage("给李四转2000元")
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending != null }
        assertTrue(checkNotNull(model.state.value.pending).mfaRequired)
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_OTP))
        ui.onNodeWithTag(UiTags.APPROVAL_OTP).performScrollTo().performTextReplacement("888888")
        hold("红色操作：强验证", "本片使用公开测试兼容码；真实设备认证通过后才可签名。本机一次性码另有实测。", "red-approval")
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_APPROVE))
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().performClick()
        mark("系统设备凭据认证", "系统窗口可能因防录屏显示黑屏。认证结果与登记设备签名由 Core 实际核验。")
        finishDevicePrompt()
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending == null }
        assertNull(model.state.value.error)
        assertEquals(12700.0, control(18800, "state").getDouble("balance"), .001)
        hold("红区真实回执", "Core 已核验并完成本笔模拟转账；未把系统认证文字当作成功。", "red-receipt")
        ui.sendChatMessage("我爱人生日是2099-10-03，锁定1000元提醒订鲜花和蛋糕")
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending != null }
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(UiTags.APPROVAL_APPROVE))
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().performClick()
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.pending == null }
        ui.onNodeWithTag(UiTags.OPEN_TASKS).performClick()
        ui.waitUntil(20_000) { !model.state.value.panelBusy && model.state.value.tasks.size == 4 }
        hold("跨场景任务", "生日锁资与提醒、订单草稿形成依赖。订单仍等待用户确认。", "tasks")
        ui.onAllNodesWithTag("task_takeover").onFirst().performScrollTo().performClick()
        ui.onNodeWithText("处理原因").performTextInput("请人工核对生日安排")
        hold("人工处理前的说明", "暂停关联待执行步骤，保留已完成操作；没有在线客服。", "takeover-dialog")
        ui.onNodeWithText("确认暂停").performClick()
        ui.waitUntil(20_000) { !model.state.value.panelBusy && model.state.value.tasks.any { it.status == "HUMAN_TAKEOVER" } }
        assertEquals(1000.0, control(18800, "state").getDouble("locked_amount"), .001)
        hold("已暂停，待人工处理", "任务保留编号与原因，锁定资金不会被自动当作退款。", "takeover")
        mark("演示结束", "本机 Mock 演示；强验证、账号隔离与异常回包另有自动化回归证据。")
        SystemClock.sleep(3000)
    }

    private fun query(text: String) {
        val before = model.state.value.messages.size
        ui.sendChatMessage(text)
        ui.waitUntil(30_000) { !model.state.value.busy && model.state.value.messages.size > before && model.state.value.messages.last().businessStatus == "SUCCEEDED" }
        assertNull(model.state.value.error)
    }

    private fun finishDevicePrompt() {
        val deadline = SystemClock.elapsedRealtime() + 15_000
        fun editable(node: android.view.accessibility.AccessibilityNodeInfo?): android.view.accessibility.AccessibilityNodeInfo? {
            if (node == null) return null
            if (node.isEditable && node.isPassword && node.isVisibleToUser) return node
            repeat(node.childCount) { editable(node.getChild(it))?.let { found -> return found } }
            return null
        }
        var field: android.view.accessibility.AccessibilityNodeInfo? = null
        while (SystemClock.elapsedRealtime() < deadline) {
            field = editable(instrumentation.uiAutomation.rootInActiveWindow)
            if (field?.isFocused == true) break
            SystemClock.sleep(150)
        }
        val pin = checkNotNull(InstrumentationRegistry.getArguments().getString("devicePin"))
        assertTrue(checkNotNull(field).performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_SET_TEXT,
            android.os.Bundle().apply { putCharSequence(android.view.accessibility.AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, pin) }))
        val time = SystemClock.uptimeMillis()
        instrumentation.uiAutomation.injectInputEvent(android.view.KeyEvent(time, time, android.view.KeyEvent.ACTION_DOWN, android.view.KeyEvent.KEYCODE_ENTER, 0), true)
        instrumentation.uiAutomation.injectInputEvent(android.view.KeyEvent(time, time + 20, android.view.KeyEvent.ACTION_UP, android.view.KeyEvent.KEYCODE_ENTER, 0), true)
        SystemClock.sleep(1000)
    }

    private fun mark(title: String, caption: String) {
        chapters.put(JSONObject().put("at_ms", System.currentTimeMillis()).put("title", title).put("caption", caption))
        File(folder, "chapters.json").writeText(chapters.toString(2))
        // UiAutomation executes an argv string, not a shell pipeline. Copy the
        // app-written file using a single executable and verify fresh bytes.
        shell("cp ${File(folder, "chapters.json").absolutePath} /data/local/tmp/anyan-demo-chapters.json")
        check(shell("cat /data/local/tmp/anyan-demo-chapters.json") == chapters.toString(2)) { "Chapter export failed" }
    }

    private fun hold(title: String, caption: String, screenshot: String) {
        mark(title, caption)
        androidx.test.espresso.Espresso.closeSoftKeyboard()
        ui.waitForIdle()
        File(folder, "$screenshot.png").outputStream().use { stream ->
            ui.onAllNodes(isRoot()).onLast().captureToImage().asAndroidBitmap().compress(Bitmap.CompressFormat.PNG, 100, stream)
        }
        shell("cp ${File(folder, "$screenshot.png").absolutePath} /data/local/tmp/anyan-demo-$screenshot.png")
        SystemClock.sleep(4500)
    }

    private fun shell(command: String): String =
        android.os.ParcelFileDescriptor.AutoCloseInputStream(instrumentation.uiAutomation.executeShellCommand(command)).use { it.bufferedReader().readText() }

    private fun control(port: Int, path: String): JSONObject {
        val connection = URL("http://127.0.0.1:$port/__scenario/$path").openConnection() as HttpURLConnection
        connection.setRequestProperty("X-Scenario-Token", token)
        if (path == "reset") {
            connection.requestMethod = "POST"; connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write("{}".toByteArray()) }
        }
        return try { JSONObject(connection.inputStream.bufferedReader().use { it.readText() }) } finally { connection.disconnect() }
    }
}
