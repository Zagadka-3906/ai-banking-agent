package com.aibanking.agent

import android.graphics.Bitmap
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.chat.AgentMarkdown
import com.aibanking.agent.ui.chat.AgentReplyCard
import com.aibanking.agent.ui.chat.ChatMessage
import com.aibanking.agent.ui.chat.ChatRole
import com.aibanking.agent.ui.theme.BankAgentTheme
import com.aibanking.agent.data.ReplyResponse
import com.aibanking.agent.data.WorkflowPlan
import com.aibanking.agent.data.WorkflowStep
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Reply rendering regressions; all fixtures are local and need no banking service. */
@RunWith(AndroidJUnit4::class)
@BankingRegression
class ReplyCardUiTest {
    @get:Rule
    val ui = createComposeRule()

    @Test
    fun markdown_formats_chinese_without_removing_card_masks_or_math() {
        render {
            AgentMarkdown("**查询结果**\n\n中文 **粗体** 正文\n\n卡号 **** 1234；算式 2 * 3 = 6。\n\n电话 138****1234 和 139****5678")
        }

        ui.onNodeWithText("查询结果", useUnmergedTree = true).assertExists()
        ui.onNodeWithText("中文 粗体 正文", useUnmergedTree = true).assertExists()
        ui.onNodeWithText("**查询结果**", substring = true, useUnmergedTree = true).assertDoesNotExist()
        ui.onNodeWithText("**粗体**", substring = true, useUnmergedTree = true).assertDoesNotExist()
        ui.onNodeWithText("**** 1234", substring = true, useUnmergedTree = true).assertExists()
        ui.onNodeWithText("2 * 3 = 6", substring = true, useUnmergedTree = true).assertExists()
        ui.onNodeWithText("电话 138****1234 和 139****5678", useUnmergedTree = true).assertExists()
    }

    @Test
    fun reply_details_are_optional_and_structured_amounts_remain_visible() {
        val message = ChatMessage(
            id = "reply-review-light",
            role = ChatRole.ASSISTANT,
            text = "**转账已完成**\n\n资金已转入收款账户，请查看下方回执。",
            riskLevel = "RED",
            traceId = "trace-reply-20260921-123456",
            result = mapOf(
                "execution_result" to mapOf(
                    "type" to "TRANSFER_SUCCESS",
                    "data" to mapOf(
                        "status" to "SUCCESS",
                        "transferred_amount" to 12345.67,
                        "remaining_available" to 8754.33,
                    ),
                ),
                "plan" to mapOf(
                    "plan_id" to "internal-plan",
                    "tasks" to listOf(mapOf("action" to "TRANSFER", "status" to "SUCCEEDED")),
                ),
            ),
        )
        val traceId = checkNotNull(message.traceId)
        render { AgentReplyCard(message) }

        ui.onNodeWithTag("reply_card_${message.id}").assertExists()
        ui.onNodeWithTag("reply_details_${message.id}").assertExists()
        ui.onNodeWithText(traceId, substring = true, useUnmergedTree = true).assertDoesNotExist()
        ui.onNodeWithText("¥12,345.67", substring = true, useUnmergedTree = true).assertExists()
        ui.onNodeWithText("¥8,754.33", substring = true, useUnmergedTree = true).assertExists()
        ui.onNodeWithText("已完成", useUnmergedTree = true).assertExists()
        ui.onNodeWithText("处理失败", useUnmergedTree = true).assertDoesNotExist()
        ui.onNodeWithText("处理步骤", useUnmergedTree = true).assertDoesNotExist()
        ui.onNodeWithText("步已完成", substring = true, useUnmergedTree = true).assertDoesNotExist()
        screenshot("reply-light")

        ui.onNodeWithText("回复详情").performScrollTo().performClick()
        ui.onNodeWithText(traceId, substring = true, useUnmergedTree = true)
            .performScrollTo().assertIsDisplayed()
        ui.onNodeWithText("收起详情").performScrollTo().performClick()
        ui.onNodeWithText(traceId, substring = true, useUnmergedTree = true).assertDoesNotExist()
        // Hiding the diagnostic identifier must not hide the actual receipt.
        ui.onNodeWithText("¥12,345.67", substring = true, useUnmergedTree = true).assertExists()
    }

    @Test
    fun bill_reply_keeps_all_metrics_and_review_reasons() {
        val message = ChatMessage(
            id = "reply-review-bill", role = ChatRole.ASSISTANT, riskLevel = "GREEN",
            text = "## 本月支出分析\n\n共核验 **6 笔交易**，请查看分类与需复核交易。",
            result = mapOf("execution_result" to mapOf(
                "type" to "BILL_ANALYZE",
                "facts" to mapOf(
                    "record_count" to 6,
                    "total_spent" to 2436.0, "refund_total" to 170.0, "net_spent" to 2266.0,
                    "category_breakdown" to listOf(
                        mapOf("category" to "日常消费", "amount" to 1600.0, "share" to 1600.0 / 2436),
                        mapOf("category" to "其他支出", "amount" to 836.0, "share" to 836.0 / 2436),
                    ),
                    "anomalies" to listOf(mapOf(
                        "merchant" to "需复核商户", "amount" to 800.0, "tx_id" to "TX-REVIEW-001",
                        "reasons" to listOf("交易金额偏高", "请核对商户"),
                    )),
                ),
            )),
        )
        render { AgentReplyCard(message) }
        ui.onNodeWithText("¥2,436.00", useUnmergedTree = true).assertExists()
        ui.onNodeWithText("¥170.00", useUnmergedTree = true).assertExists()
        ui.onNodeWithText("¥2,266.00", useUnmergedTree = true).assertExists()
        screenshot("reply-bill")
        ui.onNodeWithText("交易金额偏高、请核对商户", useUnmergedTree = true).performScrollTo().assertIsDisplayed()
    }

    @Test
    fun failure_status_is_not_overridden_by_a_green_risk_tier() {
        val message = ChatMessage(
            id = "reply-review-failure", role = ChatRole.ASSISTANT, riskLevel = "GREEN",
            text = "## 暂时无法完成查询\n\n请稍后重试。",
            result = mapOf("execution_result" to mapOf(
                "type" to "QUERY_FAILURE",
                "data" to mapOf("status" to "FAILED", "message" to "数据服务暂时不可用"),
            )),
        )
        render { AgentReplyCard(message) }
        ui.onNodeWithText("处理失败", useUnmergedTree = true).assertExists()
        ui.onNodeWithText("数据服务暂时不可用", useUnmergedTree = true).assertExists()
        ui.onNodeWithText("已完成", useUnmergedTree = true).assertDoesNotExist()
        screenshot("reply-failure")
    }

    @Test
    fun dark_reply_at_large_font_wraps_and_keeps_the_end_reachable() {
        val paragraph = "这是一段完整的账户说明，金额、收款人和处理结果都应清晰展示，文字放大后仍可继续阅读。".repeat(18)
        val ending = "末段：所有说明已完整展示，您可以继续询问。"
        val message = ChatMessage(
            id = "reply-review-dark",
            role = ChatRole.ASSISTANT,
            text = "**账户说明**\n\n$paragraph\n\n$ending",
        )
        render(darkTheme = true, fontScale = 1.6f) { AgentReplyCard(message) }

        ui.onNodeWithTag("reply_card_${message.id}").assertExists()
        val layouts = mutableListOf<TextLayoutResult>()
        ui.onNodeWithText(paragraph, substring = true, useUnmergedTree = true)
            .performSemanticsAction(SemanticsActions.GetTextLayoutResult) { getLayout ->
                assertTrue("Reply paragraph must expose its text layout", getLayout(layouts))
            }
        assertTrue("Long paragraphs must wrap", layouts.any { it.lineCount > 1 })
        layouts.forEach { layout ->
            assertFalse("Large-font reply text must not clip or ellipsize", layout.hasVisualOverflow)
            assertEquals("The final character must remain laid out", layout.layoutInput.text.length, layout.getLineEnd(layout.lineCount - 1))
        }
        screenshot("reply-dark-large-font")
        ui.onNodeWithText(ending, substring = true, useUnmergedTree = true)
            .performScrollTo().assertIsDisplayed()
    }

    @Test
    fun pending_result_shows_reconciliation_action_and_remaining_steps() {
        var calls = 0
        val message = ChatMessage(id = "reply-workflow-pending", role = ChatRole.ASSISTANT,
            text = "暂未取得可核验回执，请勿重复办理。", riskLevel = "YELLOW", businessStatus = "PENDING_RECONCILIATION",
            conversationId = "conversation-local",
            response = ReplyResponse(version = 1, operationId = "plan-local:settle", workflowPlan = WorkflowPlan(
                currentStep = 2, steps = listOf(WorkflowStep(1, "查询余额", "SUCCEEDED"),
                    WorkflowStep(2, "给李四转10元", "PENDING_RECONCILIATION"), WorkflowStep(3, "给李四转20元", "PENDING")))))
        render { AgentReplyCard(message, onReconcile = { calls++ }) }
        ui.onNodeWithText("任务进度 · 2/3", useUnmergedTree = true).assertExists()
        ui.onNodeWithText("3. 给李四转20元", useUnmergedTree = true).assertExists()
        ui.onNodeWithText("已完成", useUnmergedTree = true).assertDoesNotExist()
        ui.onNodeWithTag("reconcile_${message.id}").performScrollTo().performClick()
        assertEquals(1, calls)
        screenshot("reply-workflow-pending")
    }

    private fun render(
        darkTheme: Boolean = false,
        fontScale: Float = 1f,
        content: @Composable () -> Unit,
    ) {
        ui.setContent {
            val density = LocalDensity.current.density
            CompositionLocalProvider(LocalDensity provides Density(density, fontScale)) {
                BankAgentTheme(darkTheme = darkTheme) {
                    CompositionLocalProvider(LocalBankMotionEnabled provides false) {
                        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                            Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
                                content()
                            }
                        }
                    }
                }
            }
        }
    }

    private fun screenshot(name: String) {
        ui.waitForIdle()
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        // Capture the Compose root after its rendered frame, rather than a
        // compositor frame that can still be the Activity's starting window.
        val testOutput = InstrumentationRegistry.getArguments().getString("additionalTestOutputDir")
        val folder = if (testOutput != null) File(testOutput, "reply-review")
            else File(instrumentation.targetContext.getExternalFilesDir(null), "reply-review")
        check(folder.isDirectory || folder.mkdirs()) { "Cannot create reply screenshot folder" }
        val bitmap = ui.onRoot().captureToImage().asAndroidBitmap()
        try {
            File(folder, "$name.png").outputStream().use {
                check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) { "Cannot save reply screenshot" }
            }
        } finally {
            bitmap.recycle()
        }
    }
}
