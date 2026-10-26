package com.aibanking.agent

import android.graphics.Bitmap
import android.os.SystemClock
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.data.StageProgress
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.chat.*
import com.aibanking.agent.ui.theme.BankAgentTheme
import java.io.File
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Local UI fixtures verify actionable state, exact facts and history-reading behavior. */
@RunWith(AndroidJUnit4::class)
@BankingRegression
class ConversationComponentsUiTest {
    @get:Rule val ui = createComposeRule()
    private val summary = "向李四（尾号 1234）转账 ¥1,234.56，手续费 ¥0.00。"

    @Test fun yellow_approval_preserves_the_full_summary_and_requires_an_explicit_click() {
        var confirmed = 0
        render { ApprovalFixture(onApprove = { confirmed++ }) }
        ui.onNodeWithText(summary).assertTextFullyVisible(summary)
        ui.runOnIdle { assertEquals(0, confirmed) }
        screenshot("approval-light-large-text")
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().assertIsEnabled().performClick()
        ui.runOnIdle { assertEquals(1, confirmed) }
    }

    @Test fun red_approval_requires_six_digits_and_calls_only_the_identity_gate() {
        val otp = mutableStateOf("12345x")
        var ordinary = 0
        var biometric = 0
        render(dark = true) {
            ApprovalFixture(red = true, otp = otp.value, onOtpChange = { otp.value = it },
                onApprove = { ordinary++ }, onBiometric = { biometric++ })
        }
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().assertIsNotEnabled()
        ui.runOnIdle { otp.value = "1234567" }
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).assertIsNotEnabled()
        ui.runOnIdle { otp.value = "１２３４５６" }
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).assertIsNotEnabled()
        ui.runOnIdle { otp.value = "123456" }
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).assertIsEnabled().performClick()
        ui.runOnIdle { assertEquals(0, ordinary); assertEquals(1, biometric) }
        screenshot("approval-red-dark-large-text")
    }

    @Test fun valid_code_cannot_bypass_an_unavailable_identity_gate() {
        render { ApprovalFixture(red = true, otp = "123456", canPromptBiometric = false) }
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().assertIsNotEnabled()
        ui.onNodeWithText("本设备未录入生物识别凭据，无法完成红色风险强验证。")
            .performScrollTo().assertIsDisplayed()
    }

    @Test fun expired_approval_can_be_cancelled_but_never_confirmed() {
        var rejected = 0
        render { ApprovalFixture(expired = true, onReject = { rejected++ }) }
        ui.onNodeWithText("已超时，请重新发起操作").performScrollTo().assertIsDisplayed()
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().assertIsNotEnabled()
        ui.onNodeWithTag(UiTags.APPROVAL_REJECT).performScrollTo().performClick()
        ui.runOnIdle { assertEquals(1, rejected) }
    }

    @Test fun submitting_approval_disables_both_decisions() {
        render { ApprovalFixture(busy = true) }
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().assertIsNotEnabled()
        ui.onNodeWithTag(UiTags.APPROVAL_REJECT).performScrollTo().assertIsNotEnabled()
    }

    @Test fun timeline_updates_from_server_events_without_claiming_a_business_success() {
        val stages = mutableStateOf(listOf(
            StageProgress("read", "查询账户", "completed", 321),
            StageProgress("review", "核对办理内容", "waiting"),
        ))
        render(dark = true) { WorkflowProgress(stages.value) }
        ui.onNodeWithText("待您确认", substring = true).assertIsDisplayed()
        ui.onAllNodesWithText("ms", substring = true).assertCountEquals(0)
        ui.onAllNodesWithText("转账成功", substring = true).assertCountEquals(0)
        screenshot("workflow-dark-large-text")
        ui.runOnIdle { stages.value = listOf(StageProgress("review", "核对办理内容", "failed")) }
        ui.onNodeWithText("未完成", substring = true).assertIsDisplayed()
        ui.onNodeWithText("待您确认", substring = true).assertDoesNotExist()
        ui.runOnIdle { stages.value = listOf(StageProgress("review", "核对办理内容", "unknown")) }
        ui.onNodeWithText("状态待更新", substring = true).assertIsDisplayed()
    }

    @Test fun busy_composer_preserves_edits_and_cannot_send_with_the_keyboard() {
        val value = mutableStateOf("草稿")
        val processing = mutableStateOf(true)
        var sent = ""
        render(dark = true, scroll = false) {
            InputRow(value.value, enabled = !processing.value && value.value.isNotBlank(), voiceEnabled = false,
                captureVoiceResult = { { } }, cloudSpeechAvailable = { it(false) },
                transcribeCloud = { _, _, _ -> }, onValueChange = { value.value = it },
                onSend = { sent = value.value }, processing = processing.value)
        }
        ui.onNodeWithTag(UiTags.CHAT_INPUT).performTextReplacement("给李四转 20 元")
        ui.onNodeWithTag(UiTags.CHAT_INPUT).performImeAction()
        ui.onNodeWithTag(UiTags.CHAT_SEND).assertIsNotEnabled()
        ui.runOnIdle { assertEquals("给李四转 20 元", value.value); assertEquals("", sent) }
        screenshot("composer-dark-large-text")
        ui.runOnIdle { processing.value = false }
        ui.onNodeWithTag(UiTags.CHAT_SEND).assertIsEnabled().performClick()
        ui.runOnIdle { assertEquals("给李四转 20 元", sent) }
    }

    @Test fun financial_value_is_fully_visible_before_any_animation_frame() {
        ui.mainClock.autoAdvance = false
        render(motion = true) { ReplyValueHero("可用余额", "¥1,234.56") }
        ui.onNodeWithText("¥1,234.56").assertTextFullyVisible("¥1,234.56")
    }

    @Test fun streaming_activity_settles_into_the_verified_reply_in_the_same_slot() {
        val message = mutableStateOf(ChatMessage(id = "stream", role = ChatRole.ASSISTANT,
            text = "正在核对…", streaming = true, stages = listOf(StageProgress("read", "查询账户", "started"))))
        render { AgentReplyCard(message.value) }
        ui.onNodeWithTag("thinking_reply").assertExists()
        ui.onNodeWithText("进行中", substring = true).assertExists()
        ui.runOnIdle { message.value = message.value.copy(streaming = false,
            text = "已核对账户余额。", businessStatus = "SUCCEEDED") }
        ui.onNodeWithTag("reply_card_stream").assertExists()
        ui.onNodeWithTag("thinking_reply").assertDoesNotExist()
        ui.onNodeWithText("已核对账户余额。").assertExists()
    }

    @Test fun reading_history_keeps_its_position_when_a_reply_arrives_and_can_resume_following() {
        val count = mutableIntStateOf(30)
        lateinit var listState: LazyListState
        render(scroll = false) {
            val state = rememberLazyListState()
            listState = state
            ConversationList(state, count.intValue, "user-1", count.intValue, false, Modifier.fillMaxSize()) {
                items(count.intValue, key = { it }) { index ->
                    Text("消息 $index", Modifier.fillMaxWidth().height(80.dp).testTag("fixture_$index"))
                }
            }
        }
        ui.onNodeWithTag("fixture_29").assertIsDisplayed()
        ui.onNodeWithTag(UiTags.CHAT_LIST).performTouchInput { swipeDown() }
        ui.onNodeWithTag("chat_latest").assertIsDisplayed()
        ui.waitUntil { !listState.isScrollInProgress }
        var before = -1 to -1
        ui.runOnIdle { before = listState.firstVisibleItemIndex to listState.firstVisibleItemScrollOffset }
        ui.runOnIdle { count.intValue++ }
        ui.runOnIdle { assertEquals(before, listState.firstVisibleItemIndex to listState.firstVisibleItemScrollOffset) }
        ui.onNodeWithTag("chat_latest").performClick()
        ui.onNodeWithTag("fixture_30").assertIsDisplayed()
        ui.onNodeWithTag("chat_latest").assertDoesNotExist()
    }

    @Test fun sending_a_new_message_resumes_following_after_reading_history() {
        val user = mutableStateOf("user-1")
        render(scroll = false) {
            val state = rememberLazyListState()
            ConversationList(state, user.value, user.value, 30, false, Modifier.fillMaxSize()) {
                items(30, key = { it }) { index -> Text("消息 $index", Modifier.fillMaxWidth().height(80.dp).testTag("fixture_$index")) }
            }
        }
        ui.onNodeWithTag(UiTags.CHAT_LIST).performTouchInput { swipeDown() }
        ui.onNodeWithTag("chat_latest").assertIsDisplayed()
        ui.runOnIdle { user.value = "user-2" }
        ui.onNodeWithTag("fixture_29").assertIsDisplayed()
    }

    @Composable private fun ApprovalFixture(
        red: Boolean = false, otp: String = "", expired: Boolean = false, busy: Boolean = false,
        canPromptBiometric: Boolean = true, onOtpChange: (String) -> Unit = {},
        onApprove: () -> Unit = {}, onBiometric: () -> Unit = {}, onReject: () -> Unit = {},
    ) {
        val deadline = remember { SystemClock.elapsedRealtime() + if (expired) -1000L else 120000L }
        ApprovalCard(ApprovalView("fixture", summary = summary, mfaRequired = red, expiresIn = 120),
            deadlineMillis = deadline, otp = otp, busy = busy, canPromptBiometric = canPromptBiometric,
            onOtpChange = onOtpChange, onRequestOtp = {}, onReject = onReject,
            onApprove = onApprove, onBiometricApprove = onBiometric)
    }

    private fun render(dark: Boolean = false, scroll: Boolean = true, motion: Boolean = false, content: @Composable () -> Unit) {
        ui.setContent {
            CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density, 1.6f)) {
                BankAgentTheme(darkTheme = dark) {
                    CompositionLocalProvider(LocalBankMotionEnabled provides motion) {
                        Surface(Modifier.size(320.dp, 600.dp), color = MaterialTheme.colorScheme.background) {
                            if (scroll) Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp)) { content() }
                            else content()
                        }
                    }
                }
            }
        }
    }

    private fun screenshot(name: String) {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val extra = InstrumentationRegistry.getArguments().getString("additionalTestOutputDir")
        val folder = (if (extra != null) File(extra, "native-conversation")
            else File(context.getExternalFilesDir(null), "native-conversation")).apply { mkdirs() }
        val bitmap = ui.onRoot().captureToImage().asAndroidBitmap()
        try { File(folder, "$name.png").outputStream().use { check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) } }
        finally { bitmap.recycle() }
    }
}
