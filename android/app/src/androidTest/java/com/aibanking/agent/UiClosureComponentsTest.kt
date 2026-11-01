package com.aibanking.agent

import android.app.Application
import android.graphics.Bitmap
import android.os.SystemClock
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.core.app.ApplicationProvider
import androidx.test.espresso.Espresso
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.data.*
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.chat.*
import com.aibanking.agent.ui.theme.BankAgentTheme
import java.io.File
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

@BankingRegression
class UiClosureComponentsTest {
    @get:Rule val ui = createComposeRule()

    @Test fun consuming_a_message_does_not_cancel_the_visible_snackbar_or_lose_the_next_one() {
        val message = mutableStateOf<String?>("第一条通知")
        lateinit var host: SnackbarHostState
        render {
            host = remember { SnackbarHostState() }
            ChatMessageEffects(null, message.value, host, { _, notice ->
                if (message.value == notice) message.value = null
            })
            SnackbarHost(host)
        }
        ui.onNodeWithText("第一条通知").assertIsDisplayed()
        ui.runOnIdle { assertNull(message.value); message.value = "第二条通知" }
        ui.mainClock.advanceTimeByFrame()
        ui.onNodeWithText("第一条通知").assertIsDisplayed()
        ui.runOnIdle { host.currentSnackbarData!!.dismiss() }
        ui.onNodeWithText("第二条通知").assertIsDisplayed()
    }

    @Test fun changing_accounts_discards_the_previous_notice_queue() {
        val owner = mutableStateOf("alice")
        val notice = mutableStateOf<String?>("旧账号通知")
        render {
            val host = remember { SnackbarHostState() }
            ChatMessageEffects(null, notice.value, host,
                { _, consumed -> if (notice.value == consumed) notice.value = null }, owner.value)
            SnackbarHost(host)
        }
        ui.onNodeWithText("旧账号通知").assertIsDisplayed()
        ui.runOnIdle { notice.value = "旧账号排队通知" }
        ui.waitForIdle()
        ui.runOnIdle { owner.value = "bob"; notice.value = "新账号通知" }
        ui.onNodeWithText("新账号通知").assertIsDisplayed()
        ui.onNodeWithText("旧账号通知").assertDoesNotExist()
        ui.onNodeWithText("旧账号排队通知").assertDoesNotExist()
    }

    @Test fun deleting_a_contact_requires_a_named_confirmation_and_cancel_keeps_it() {
        val contact = ContactView(contactId = "original", name = "李四", phone = "12345")
        val model = model(ChatUiState(authenticated = true, showContacts = true, contactsScreen = ContactsScreen.DETAIL,
            selectedContactId = contact.contactId, contacts = listOf(contact)))
        render { val state by model.state.collectAsState(); ContactsUi(state, model) }
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE).performClick()
        ui.onNodeWithText("删除联系人？").assertIsDisplayed()
        ui.onNodeWithText("确定删除“李四”？该联系人将从通讯录移除，已有转账记录不受影响。").assertIsDisplayed()
        ui.runOnIdle { assertEquals(listOf(contact), model.state.value.contacts) }
        screenshot("contact-delete-confirmation")
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE_CANCEL).performClick()
        ui.onNodeWithText("删除联系人？").assertDoesNotExist()
        ui.runOnIdle { assertEquals(listOf(contact), model.state.value.contacts); assertNull(model.state.value.contactDeleteId) }
    }

    @Test fun one_yuan_purchase_shortcuts_and_invalid_input_feedback_keep_the_actual_amount() {
        val product = ProductView(productId = "LX00118", name = "现金管理", riskLevel = "R1", minAmount = 1.0)
        val model = model(ChatUiState(authenticated = true, selectedProductId = product.productId,
            wealthProducts = listOf(product), riskProfile = RiskProfileView("C5")))
        render { val state by model.state.collectAsState(); PurchaseBar(state, model, product) }
        ui.onNodeWithText("起购 ¥1").performClick()
        ui.onNodeWithTag(UiTags.WEALTH_AMOUNT).assertTextContains("1")
        ui.onNodeWithTag(UiTags.WEALTH_AMOUNT).performTextReplacement("1.001")
        ui.onNodeWithText("请输入合法金额，最多两位小数").assertIsDisplayed()
        ui.runOnIdle { model.dismissMessages(); assertEquals("1", model.state.value.buyAmount) }
        ui.onNodeWithText("请输入合法金额，最多两位小数").assertIsDisplayed()
        ui.onNodeWithTag(UiTags.WEALTH_AMOUNT).performTextReplacement("1.01")
        ui.onNodeWithText("请输入合法金额，最多两位小数").assertDoesNotExist()
        ui.onNodeWithText("+100").performClick()
        ui.runOnIdle { assertEquals("101.01", model.state.value.buyAmount) }
        screenshot("purchase-one-yuan")
    }

    @Test fun contact_server_error_stays_in_the_form_until_a_field_is_edited() {
        val model = model(ChatUiState(showContacts = true, contactsScreen = ContactsScreen.ADD,
            contactName = "李四", contactPhone = "12345", contactFormError = "该电话已存在，请核对联系人"))
        render { val state by model.state.collectAsState(); ContactsUi(state, model) }
        ui.onNodeWithText("该电话已存在，请核对联系人").performScrollTo().assertIsDisplayed()
        ui.runOnIdle { model.dismissMessages() }
        ui.onNodeWithText("该电话已存在，请核对联系人").assertIsDisplayed()
        ui.onNodeWithText("电话").performScrollTo().performTextReplacement("54321")
        ui.onNodeWithText("该电话已存在，请核对联系人").assertDoesNotExist()
    }

    @Test fun wealth_style_rows_keep_labels_values_and_descriptions_readable_at_large_text() {
        val label = "长期投资风格评价指标"
        val value = "123456789.01%"
        val description = "这些是产品提供的指标说明，不代表未来收益。"
        val product = ProductView(productId = "test", name = "布局验收", riskLevel = "R1", minAmount = 1.0,
            analysisMetrics = listOf(AnalysisMetricView(label = label, value = value, score = 3, description = description)))
        val model = model(ChatUiState(showWealth = true, wealthScreen = WealthScreen.DETAIL,
            selectedProductId = product.productId, wealthProducts = listOf(product)))
        render {
            val state by model.state.collectAsState()
            WealthDetailScreen(state, model, remember { SnackbarHostState() })
        }
        listOf(label, value, description).forEach { text ->
            ui.onNodeWithText(text, useUnmergedTree = true).performScrollTo().assertTextFullyVisible(text)
        }
        val layouts = mutableListOf<androidx.compose.ui.text.TextLayoutResult>()
        ui.onNodeWithText(value, useUnmergedTree = true)
            .performSemanticsAction(androidx.compose.ui.semantics.SemanticsActions.GetTextLayoutResult) { it(layouts) }
        assertTrue(layouts.isNotEmpty())
        assertTrue("The financial value must stay together at 1.6x text", layouts.all { it.lineCount == 1 })
        screenshot("wealth-style-rows", UiTags.WEALTH_PROFILE)
    }

    @Test fun card_number_error_and_server_error_clear_only_after_editing() {
        val model = model(ChatUiState(showCards = true, cardFormError = "该卡已绑定，请核对卡号"))
        render { val state by model.state.collectAsState(); CardsUi(state, model) }
        ui.onNodeWithTag(UiTags.CARD_OPEN_ADD).performClick()
        ui.onNodeWithText("该卡已绑定，请核对卡号").performScrollTo().assertIsDisplayed()
        ui.runOnIdle { model.dismissMessages() }
        ui.onNodeWithText("该卡已绑定，请核对卡号").assertIsDisplayed()
        ui.onNodeWithTag(UiTags.CARD_NUMBER).performScrollTo().performTextInput("1234")
        ui.onNodeWithText("该卡已绑定，请核对卡号").assertDoesNotExist()
        ui.onNodeWithText("请输入通过校验的 12–19 位银行卡号").assertIsDisplayed()
        ui.onNodeWithTag(UiTags.CARD_NUMBER).performTextReplacement("4111111111111111")
        ui.onNodeWithText("请输入通过校验的 12–19 位银行卡号").assertDoesNotExist()
    }

    @Test fun approval_failure_remains_inline_while_other_notices_are_consumed() {
        val model = model(ChatUiState(pending = ApprovalView("approval", mfaRequired = true), approvalError = "验证码已失效，请重新获取"))
        render {
            val state by model.state.collectAsState()
            ApprovalCard(state.pending!!, deadlineMillis = SystemClock.elapsedRealtime() + 60_000,
                otp = state.otp, busy = false, canPromptBiometric = true,
                onOtpChange = model::onOtpChange, onRequestOtp = {}, onReject = {}, onApprove = {},
                onBiometricApprove = {}, error = state.approvalError)
        }
        ui.onNodeWithText("验证码已失效，请重新获取").assertExists()
        ui.runOnIdle { model.dismissMessages() }
        ui.onNodeWithText("验证码已失效，请重新获取").assertExists()
        ui.onNodeWithTag(UiTags.APPROVAL_OTP).performTextInput("123456")
        ui.onNodeWithText("验证码已失效，请重新获取").assertDoesNotExist()
    }

    @Test fun task_read_loading_allows_back_without_submitting_an_operation() {
        var dismissed = 0
        var submitted = 0
        render {
            TaskPanel(emptyList(), emptyList(), true,
                { submitted++ }, { _, _ -> submitted++ }, { submitted++ }, { submitted++ },
                { dismissed++ }, submitting = false)
        }
        ui.onNodeWithText("正在更新任务…").assertIsDisplayed()
        Espresso.pressBack()
        ui.runOnIdle {
            assertEquals(1, dismissed)
            assertEquals(0, submitted)
        }
    }

    @Test fun task_submission_blocks_back_until_the_operation_finishes() {
        val submitting = mutableStateOf(true)
        var dismissed = 0
        render {
            TaskPanel(emptyList(), emptyList(), submitting.value,
                {}, { _, _ -> }, {}, {}, { dismissed++ }, submitting = submitting.value)
        }
        Espresso.pressBack()
        ui.runOnIdle { assertEquals(0, dismissed); submitting.value = false }
        Espresso.pressBack()
        ui.runOnIdle { assertEquals(1, dismissed) }
    }

    @Test fun task_confirmation_and_back_use_one_modal_and_never_submit_on_navigation() {
        var submitted = 0
        var dismissed = 0
        render {
            TaskPanel(listOf(TaskView("task", "SCHEDULED_TRANSFER", "PENDING")), emptyList(), false,
                {}, { _, _ -> submitted++ }, {}, {}, { dismissed++ })
        }
        ui.onNodeWithTag("task_takeover").performScrollTo().performClick()
        ui.onAllNodes(isDialog()).assertCountEquals(1)
        ui.onNodeWithTag("task_takeover_confirm").assertIsNotEnabled()
        ui.onNodeWithTag("task_takeover_reason").performTextInput("核对日期")
        Espresso.closeSoftKeyboard()
        Espresso.pressBack()
        ui.onNodeWithTag("task_takeover").assertExists()
        ui.runOnIdle { assertEquals(0, submitted); assertEquals(0, dismissed) }
    }

    @Test fun task_confirmation_retains_reason_and_error_after_a_failed_request() {
        val error = mutableStateOf<String?>(null)
        var reason: String? = null
        render {
            TaskPanel(listOf(TaskView("task", "SCHEDULED_TRANSFER", "PENDING")), emptyList(), false,
                {}, { _, input -> reason = input; error.value = "暂停未成功，请核对后重试" }, {}, {}, {},
                error = error.value, onClearError = { error.value = null })
        }
        ui.onNodeWithTag("task_takeover").performScrollTo().performClick()
        ui.onNodeWithTag("task_takeover_reason").performTextInput("核对日期")
        Espresso.closeSoftKeyboard()
        ui.onNodeWithTag("task_takeover_confirm").performScrollTo().performClick()
        ui.onNodeWithText("暂停未成功，请核对后重试").assertIsDisplayed()
        ui.runOnIdle { assertEquals("核对日期", reason) }
        ui.onAllNodes(isDialog()).assertCountEquals(1)
        screenshot("task-inline-confirmation", "task_panel")
    }

    private fun model(state: ChatUiState): ChatViewModel {
        val app = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(app).clear()
        lateinit var model: ChatViewModel
        ui.runOnUiThread { model = ChatViewModel(app) }
        ui.waitUntil { !model.state.value.restoring }
        ui.runOnUiThread { model._state.value = state }
        return model
    }

    private fun render(content: @Composable () -> Unit) {
        ui.setContent {
            CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density, 1.6f)) {
                BankAgentTheme(darkTheme = true) {
                    CompositionLocalProvider(LocalBankMotionEnabled provides false) {
                        Surface(Modifier.size(320.dp, 620.dp), color = MaterialTheme.colorScheme.background) { content() }
                    }
                }
            }
        }
    }

    private fun screenshot(name: String, tag: String? = null) {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val extra = InstrumentationRegistry.getArguments().getString("additionalTestOutputDir")
        val folder = (if (extra != null) File(extra, "ui-closure") else File(context.getExternalFilesDir(null), "ui-closure")).apply { mkdirs() }
        val bitmap = (if (tag == null) ui.onRoot() else ui.onNodeWithTag(tag)).captureToImage().asAndroidBitmap()
        try { File(folder, "$name.png").outputStream().use { check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) } }
        finally { bitmap.recycle() }
    }
}
