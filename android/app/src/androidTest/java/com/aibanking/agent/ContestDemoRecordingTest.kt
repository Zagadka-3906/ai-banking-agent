package com.aibanking.agent

import android.graphics.Bitmap
import android.os.SystemClock
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.data.ContactRepository
import com.aibanking.agent.data.SessionStore
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.*
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

/** Explicit recording selector only; all changes use disposable 18800/18810 stores. */
class ContestDemoRecordingTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()
    private lateinit var model: ChatViewModel
    private val args get() = InstrumentationRegistry.getArguments()
    private val folder get() = File(InstrumentationRegistry.getInstrumentation().targetContext.getExternalFilesDir(null), "contest-demo").apply { mkdirs() }
    private val chapters = JSONArray()

    @Test fun record_current_submission_demo() {
        assumeTrue(BuildConfig.APPLICATION_ID.endsWith(".scenariotest"))
        ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
        ui.waitUntil(20_000) { !model.state.value.restoring }
        if (model.state.value.authenticated) ui.runOnIdle { model.logout() }
        control(18810); control(18800)
        ui.ensureLoggedIn("Bank@2026")
        ui.waitUntil(20_000) { model.state.value.authenticated }
        mark("首页", "文字对话与业务入口，所有账户均为虚构数据", "home")
        ui.sendChatMessage("查一下余额")
        ui.waitUntil(25_000) { !model.state.value.busy && model.state.value.messages.any { it.businessStatus == "SUCCEEDED" } }
        mark("绿区查询", "查询只读本人余额，不创建付款授权", "balance")
        ui.sendChatMessage("给李四转10元")
        ui.waitUntil(25_000) { model.state.value.pending != null }
        assertFalse(checkNotNull(model.state.value.pending).mfaRequired)
        mark("黄区确认", "核对收款人和10元金额，再由用户确认", "yellow")
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().performClick()
        ui.waitUntil(25_000) { !model.state.value.busy && model.state.value.pending == null }
        ui.onNodeWithTag(UiTags.OUTCOME_SCREEN).assertIsDisplayed()
        mark("核心回执", "本段实际完成10元模拟转账，结果来自核心回执", "receipt")
        ui.returnFromOutcome()
        ui.sendChatMessage("给李四转2000元")
        ui.waitUntil(25_000) { model.state.value.pending != null }
        assertTrue(checkNotNull(model.state.value.pending).mfaRequired)
        mark("红区要求", "大额需要额外验证；本段展示要求后取消，不执行付款", "red")
        ui.onNodeWithTag(UiTags.APPROVAL_REJECT).performScrollTo().performClick()
        ui.waitUntil(20_000) { !model.state.value.busy && model.state.value.pending == null }
        ui.returnFromOutcome()
        val repo = ContactRepository.create()
        val session = checkNotNull(SessionStore(ui.activity).sessionToken())
        val contact = runBlocking { repo.add("user_zhang", session, "演示联系人", "19900009999", "朋友", null, UUID.randomUUID().toString()) }
        ui.openFromDrawer(UiTags.OPEN_CONTACTS)
        ui.waitUntil(20_000) { !model.state.value.panelBusy && model.state.value.contacts.any { it.contactId == contact.contactId } }
        ui.onNodeWithText("演示联系人").performScrollTo().performClick()
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE).performClick()
        mark("删除前确认", "取消不会删除；确认后才请求服务器删除通讯录记录", "contact-confirm")
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE_CONFIRM).performClick()
        ui.waitUntil(20_000) { model.state.value.contactDeletingId == null && model.state.value.contacts.none { it.contactId == contact.contactId } }
        assertTrue(runBlocking { repo.list("user_zhang", session) }.none { it.contactId == contact.contactId })
        mark("同步完成", "服务器通讯录已核对，重新读取也不显示已删除联系人", "contact-deleted")
        ui.openFromDrawer(UiTags.OPEN_CARDS)
        ui.waitUntil(20_000) { !model.state.value.panelBusy }
        mark("卡片管理", "纯色卡包界面，状态、额度和交易渠道分别展示", "cards")
        ui.openFromDrawer(UiTags.OPEN_WEALTH)
        ui.waitUntil(20_000) { !model.state.value.panelBusy }
        mark("理财货架", "展示虚构产品，不代表已持有；申购另需测评与授权", "wealth")
        File(folder, "chapters.json").writeText(chapters.toString(2))
        // Orchestrator clears app storage after the test. Export through the
        // test shell before teardown so the recorder can pull the real frames.
        for (file in checkNotNull(folder.listFiles())) {
            shell("cp ${file.absolutePath} /data/local/tmp/anyan-contest-${file.name}")
        }
        check(shell("cat /data/local/tmp/anyan-contest-chapters.json") == chapters.toString(2)) { "Unable to export demo evidence" }
    }

    private fun mark(title: String, caption: String, name: String) {
        ui.waitForIdle()
        chapters.put(JSONObject().put("title", title).put("caption", caption).put("elapsed_ms", SystemClock.elapsedRealtime()).put("image", "$name.png"))
        File(folder, "chapters.json").writeText(chapters.toString(2))
        val image = ui.onRoot().captureToImage().asAndroidBitmap()
        File(folder, "$name.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }
        image.recycle()
        SystemClock.sleep(3500)
    }

    private fun control(port: Int) {
        val c = URL("http://127.0.0.1:$port/__scenario/reset").openConnection() as HttpURLConnection
        c.requestMethod = "POST"; c.doOutput = true
        c.setRequestProperty("X-Scenario-Token", checkNotNull(args.getString("controlToken")))
        c.setRequestProperty("Content-Type", "application/json")
        c.outputStream.use { it.write("{}".toByteArray()) }
        try { check(c.responseCode == 200) } finally { c.disconnect() }
    }

    private fun shell(command: String): String = InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand(command)
        .let { android.os.ParcelFileDescriptor.AutoCloseInputStream(it).bufferedReader().use { reader -> reader.readText() } }
}
