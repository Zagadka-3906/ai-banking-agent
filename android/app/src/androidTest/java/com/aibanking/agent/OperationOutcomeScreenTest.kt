package com.aibanking.agent

import android.graphics.Bitmap
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarDuration
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.data.ReplyResponse
import com.aibanking.agent.data.WorkflowPlan
import com.aibanking.agent.data.WorkflowStep
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.chat.ChatMessage
import com.aibanking.agent.ui.chat.ChatRole
import com.aibanking.agent.ui.chat.OperationOutcomeScreen
import com.aibanking.agent.ui.chat.OperationProblem
import com.aibanking.agent.ui.chat.outcomeFor
import com.aibanking.agent.ui.theme.BankAgentTheme
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Local UI fixtures: these tests never call the banking services. */
@RunWith(AndroidJUnit4::class)
@BankingRegression
class OperationOutcomeScreenTest {
    @get:Rule val ui = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun toolbar_footer_and_system_back_only_return_to_the_conversation() {
        val calls = Calls()
        render { ResultScreen(success(), calls) }
        ui.onNodeWithTag(UiTags.OUTCOME_BACK).performClick()
        ui.onNodeWithTag(UiTags.OUTCOME_RETURN).assertIsDisplayed().performClick()
        InstrumentationRegistry.getInstrumentation().sendKeyDownUpSync(KeyEvent.KEYCODE_BACK)
        ui.waitForIdle()
        ui.runOnIdle {
            assertEquals(3, calls.back)
            assertEquals(0, calls.balance + calls.tasks + calls.reconcile)
        }
    }

    @Test
    fun success_actions_wait_for_a_gesture_and_balance_respects_the_busy_state() {
        val calls = Calls()
        val canQuery = mutableStateOf(false)
        render { ResultScreen(success(), calls, canQuery = canQuery.value) }
        ui.onNodeWithTag(UiTags.OUTCOME_BALANCE).performScrollTo().assertIsNotEnabled().performClick()
        ui.runOnIdle {
            assertEquals(0, calls.balance + calls.tasks + calls.reconcile)
            canQuery.value = true
        }
        ui.onNodeWithTag(UiTags.OUTCOME_BALANCE).assertIsEnabled().performClick()
        ui.onNodeWithTag(UiTags.OUTCOME_TASKS).performScrollTo().assertIsEnabled().performClick()
        ui.runOnIdle {
            assertEquals(1, calls.balance)
            assertEquals(1, calls.tasks)
            assertEquals(0, calls.back + calls.reconcile)
        }
    }

    @Test
    fun reconciliation_needs_receipt_identifiers_and_cannot_be_requested_twice_while_running() {
        val calls = Calls()
        val message = mutableStateOf(pending().copy(reconciling = true))
        render { ResultScreen(message.value, calls) }
        ui.onNodeWithTag(UiTags.OUTCOME_RECONCILE).performScrollTo().assertIsNotEnabled().performClick()
        ui.onNodeWithTag(UiTags.OUTCOME_BALANCE).assertDoesNotExist()
        ui.onNodeWithTag(UiTags.OUTCOME_TASKS).assertDoesNotExist()
        ui.runOnIdle {
            assertEquals(0, calls.reconcile)
            message.value = message.value.copy(reconciling = false, reconciliationError = "暂时无法核对，请稍后再试。")
        }
        ui.onNodeWithTag(UiTags.OUTCOME_RECONCILIATION_ERROR).performScrollTo().assertIsDisplayed()
        ui.onNodeWithTag(UiTags.OUTCOME_TITLE).assertTextEquals("办理结果待核对")
        ui.onNodeWithTag(UiTags.OUTCOME_RECONCILE).performScrollTo().assertIsEnabled().performClick()
        ui.runOnIdle {
            assertEquals(1, calls.reconcile)
            message.value = message.value.copy(conversationId = null)
        }
        ui.onNodeWithTag(UiTags.OUTCOME_RECONCILE).assertDoesNotExist()
        ui.onNodeWithText("此回执缺少核对信息，请返回对话查看现有回执和账户记录。")
            .performScrollTo().assertIsDisplayed()
        ui.runOnIdle {
            message.value = pending().copy(response = ReplyResponse(operationId = " "))
        }
        ui.onNodeWithTag(UiTags.OUTCOME_RECONCILE).assertDoesNotExist()
        ui.runOnIdle { assertEquals(1, calls.reconcile) }
    }

    @Test
    fun failure_and_unknown_transport_result_do_not_offer_a_resubmit_action() {
        val calls = Calls()
        val problem = mutableStateOf(OperationProblem("验证码错误，请核对后重试。", "MFA_FAILED", 403))
        render {
            OperationOutcomeScreen(
                outcomeFor(problem.value), null, problem.value, true,
                { calls.back++ }, { calls.balance++ }, { calls.tasks++ }, { calls.reconcile++ },
            )
        }
        ui.onNodeWithTag(UiTags.OUTCOME_TITLE).assertTextEquals("验证未通过")
        assertNoWriteOrQueryActions()
        ui.runOnIdle { problem.value = OperationProblem("连接已中断。", null, 0) }
        ui.onNodeWithTag(UiTags.OUTCOME_TITLE).assertTextEquals("暂未取得办理结果")
        ui.onNodeWithText("结果待核对", useUnmergedTree = true).assertExists()
        assertNoWriteOrQueryActions()
        ui.onNodeWithTag(UiTags.OUTCOME_RETURN).assertIsDisplayed().performClick()
        ui.runOnIdle {
            assertEquals(1, calls.back)
            assertEquals(0, calls.balance + calls.tasks + calls.reconcile)
        }
    }

    @Test
    fun trace_only_receipt_can_be_opened_and_resets_for_a_different_reply() {
        val message = mutableStateOf(success().copy(result = null, traceId = "trace-first-receipt"))
        render { ResultScreen(message.value, Calls()) }
        ui.onNodeWithText("trace-first-receipt", useUnmergedTree = true).assertDoesNotExist()
        ui.onNodeWithTag(UiTags.OUTCOME_RECEIPT).performScrollTo().performClick()
        ui.onNodeWithText("trace-first-receipt", useUnmergedTree = true).performScrollTo().assertIsDisplayed()
        ui.onNodeWithTag(UiTags.OUTCOME_RECEIPT).performScrollTo().performClick()
        ui.onNodeWithText("trace-first-receipt", useUnmergedTree = true).assertDoesNotExist()
        ui.onNodeWithTag(UiTags.OUTCOME_RECEIPT).performClick()
        ui.runOnIdle { message.value = message.value.copy(id = "another-reply", traceId = "trace-second-receipt") }
        ui.onNodeWithText("trace-first-receipt", useUnmergedTree = true).assertDoesNotExist()
        ui.onNodeWithText("trace-second-receipt", useUnmergedTree = true).assertDoesNotExist()
        ui.onNodeWithTag(UiTags.OUTCOME_RECEIPT).performScrollTo().performClick()
        ui.onNodeWithText("trace-second-receipt", useUnmergedTree = true).performScrollTo().assertIsDisplayed()
        ui.runOnIdle { message.value = message.value.copy(id = "no-details", traceId = " ") }
        ui.onNodeWithTag(UiTags.OUTCOME_RECEIPT).assertDoesNotExist()
    }

    @Test
    fun narrow_dark_large_text_screen_keeps_steps_receipt_and_return_reachable() {
        val label = "给收款人李四转账并逐项核对完整回执，确认金额、状态及后续待办。".repeat(3)
        val message = pending().copy(
            text = "核对中的操作尚未取得最终回执，请勿重复提交。".repeat(16),
            traceId = "trace-large-font-last-line",
            response = pending().response!!.copy(workflowPlan = WorkflowPlan(
                currentStep = 2,
                steps = listOf(
                    WorkflowStep(1, "查询可用余额", "SUCCEEDED"),
                    WorkflowStep(2, label, "PENDING_RECONCILIATION"),
                    WorkflowStep(3, "最后一步仍待办理", "PENDING"),
                ),
            )),
        )
        render(narrow = true) { ResultScreen(message, Calls()) }
        ui.onNodeWithTag(UiTags.OUTCOME_RETURN).assertIsDisplayed()
        val layouts = mutableListOf<TextLayoutResult>()
        ui.onNodeWithText("2. $label", useUnmergedTree = true)
            .performSemanticsAction(SemanticsActions.GetTextLayoutResult) { getLayout -> assertTrue(getLayout(layouts)) }
        assertTrue(layouts.any { it.lineCount > 1 })
        layouts.forEach {
            assertFalse("The complete step label must remain laid out", it.hasVisualOverflow)
            assertEquals(it.layoutInput.text.length, it.getLineEnd(it.lineCount - 1))
        }
        ui.onNodeWithText("3. 最后一步仍待办理", useUnmergedTree = true).performScrollTo().assertIsDisplayed()
        ui.onNodeWithTag(UiTags.OUTCOME_RECEIPT).performScrollTo().performClick()
        ui.onNodeWithText("trace-large-font-last-line", useUnmergedTree = true).performScrollTo().assertIsDisplayed()
        ui.onNodeWithTag(UiTags.OUTCOME_RECONCILE).performScrollTo().assertIsDisplayed()
        ui.onNodeWithTag(UiTags.OUTCOME_RETURN).assertIsDisplayed()
        screenshot("outcome-dark-large-font")
    }

    @Test
    fun result_page_displays_the_shared_error_snackbar() {
        render {
            val snackbar = remember { SnackbarHostState() }
            LaunchedEffect(Unit) { snackbar.showSnackbar("核对服务暂不可用，请稍后再试。") }
            ResultScreen(pending(), Calls(), snackbar = snackbar)
        }
        ui.onNodeWithText("核对服务暂不可用，请稍后再试。").assertIsDisplayed()
        ui.onNodeWithTag(UiTags.OUTCOME_RETURN).assertIsDisplayed()
        screenshot("outcome-reconciliation-error")
    }

    @Test
    fun a_notice_never_covers_the_task_shortcut_touch_target() {
        val calls = Calls()
        render {
            val snackbar = remember { SnackbarHostState() }
            LaunchedEffect(Unit) { snackbar.showSnackbar("会话已建立，但本机设备未注册：红色风险操作暂不可用",
                duration = SnackbarDuration.Indefinite) }
            ResultScreen(success(), calls, snackbar = snackbar)
        }
        ui.onNodeWithText("会话已建立，但本机设备未注册：红色风险操作暂不可用").assertIsDisplayed()
        ui.onNodeWithTag(UiTags.OUTCOME_TASKS).performScrollTo().assertIsDisplayed().performClick()
        ui.runOnIdle { assertEquals(1, calls.tasks); assertEquals(0, calls.back) }
        ui.onNodeWithText("会话已建立，但本机设备未注册：红色风险操作暂不可用").assertIsDisplayed()
        screenshot("outcome-notice-actions")
    }

    private fun assertNoWriteOrQueryActions() {
        ui.onNodeWithTag(UiTags.OUTCOME_BALANCE).assertDoesNotExist()
        ui.onNodeWithTag(UiTags.OUTCOME_TASKS).assertDoesNotExist()
        ui.onNodeWithTag(UiTags.OUTCOME_RECONCILE).assertDoesNotExist()
        ui.onNodeWithText("重新提交").assertDoesNotExist()
    }

    @Composable
    private fun ResultScreen(
        message: ChatMessage,
        calls: Calls,
        canQuery: Boolean = true,
        snackbar: SnackbarHostState = remember { SnackbarHostState() },
    ) {
        OperationOutcomeScreen(
            checkNotNull(outcomeFor(message)), message, null, canQuery,
            { calls.back++ }, { calls.balance++ }, { calls.tasks++ }, { calls.reconcile++ },
            snackbarHostState = snackbar,
        )
    }

    private fun render(narrow: Boolean = false, content: @Composable () -> Unit) {
        ui.activityRule.scenario.onActivity { it.enableEdgeToEdge() }
        ui.setContent {
            val density = LocalDensity.current.density
            CompositionLocalProvider(
                LocalDensity provides Density(density, if (narrow) 1.6f else 1f),
                LocalBankMotionEnabled provides false,
            ) {
                BankAgentTheme(darkTheme = narrow) {
                    Box(if (narrow) Modifier.size(320.dp, 480.dp) else Modifier.fillMaxSize()) { content() }
                }
            }
        }
    }

    private fun success() = ChatMessage(
        id = "outcome-success", role = ChatRole.ASSISTANT, text = "转账已完成，请核对回执。",
        businessStatus = "SUCCEEDED", result = mapOf("execution_result" to mapOf(
            "type" to "TRANSFER_SUCCESS", "data" to mapOf("transferred_amount" to 10.0, "status" to "SUCCESS"),
        )),
    )

    private fun pending() = ChatMessage(
        id = "outcome-pending", role = ChatRole.ASSISTANT, text = "暂未取得最终回执，请先核对。",
        businessStatus = "PENDING_RECONCILIATION", conversationId = "conversation-local",
        response = ReplyResponse(version = 1, status = "PENDING_RECONCILIATION", operationId = "operation-local"),
    )

    private class Calls(var back: Int = 0, var balance: Int = 0, var tasks: Int = 0, var reconcile: Int = 0)

    private fun screenshot(name: String) {
        ui.waitForIdle()
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val testOutput = InstrumentationRegistry.getArguments().getString("additionalTestOutputDir")
        val folder = if (testOutput != null) File(testOutput, "outcome-review")
            else File(instrumentation.targetContext.getExternalFilesDir(null), "outcome-review")
        check(folder.isDirectory || folder.mkdirs())
        val bitmap = ui.onRoot().captureToImage().asAndroidBitmap()
        try {
            File(folder, "$name.png").outputStream().use { check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) }
        } finally {
            bitmap.recycle()
        }
    }
}
