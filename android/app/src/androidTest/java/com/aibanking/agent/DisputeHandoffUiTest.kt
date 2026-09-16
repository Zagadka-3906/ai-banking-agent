package com.aibanking.agent

import android.Manifest
import androidx.compose.ui.test.ComposeTimeoutException
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.printToLog
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import com.aibanking.agent.ui.UiTags
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** 争议材料收集后交人工客服：黄色确认，不自动退款。 */
@RunWith(AndroidJUnit4::class)
class DisputeHandoffUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()
    @get:Rule val notifications: GrantPermissionRule = GrantPermissionRule.grant(Manifest.permission.POST_NOTIFICATIONS)

    @Test
    fun dispute_is_handed_to_human_without_refund() {
        ui.ensureLoggedIn("Bank@2026", 30_000L)
        ui.sendChatMessage("对 TX1004 申请争议退款，陈述：本人未在美国消费，凭证 EVIDENCE-001，转人工客服")
        try {
            ui.waitUntil(timeoutMillis = 60_000L) { ui.onAllNodesWithTag(UiTags.APPROVAL_APPROVE).fetchSemanticsNodes().isNotEmpty() }
        } catch (timeout: ComposeTimeoutException) {
            ui.onRoot().printToLog("DISPUTE_HANDOFF")
            throw timeout
        }
        assertTrue(ui.onAllNodesWithText("黄色", substring = true).fetchSemanticsNodes().isNotEmpty())
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performClick()
        ui.waitUntil(timeoutMillis = 60_000L) { ui.onAllNodesWithTag(UiTags.APPROVAL_APPROVE).fetchSemanticsNodes().isEmpty() }
        ui.waitUntil(timeoutMillis = 60_000L) {
            ui.onAllNodesWithText("人工客服", substring = true).fetchSemanticsNodes().isNotEmpty() &&
                ui.onAllNodesWithText("未执行退款", substring = true).fetchSemanticsNodes().isNotEmpty()
        }
    }
}
