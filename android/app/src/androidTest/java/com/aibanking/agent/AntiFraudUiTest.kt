package com.aibanking.agent

import android.Manifest
import androidx.compose.ui.test.ComposeTimeoutException
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.printToLog
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import com.aibanking.agent.ui.UiTags
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** 反诈处置助手真实红色审批链：核验交易 → MFA → 冻结卡 → 建立人工审核工单。 */
@RunWith(AndroidJUnit4::class)
class AntiFraudUiTest {
    @get:Rule
    val ui = createAndroidComposeRule<MainActivity>()

    @get:Rule
    val notifications: GrantPermissionRule = GrantPermissionRule.grant(Manifest.permission.POST_NOTIFICATIONS)

    @Test
    fun suspicious_overseas_purchase_requires_red_mfa_and_creates_dispute() {
        ui.ensureLoggedIn(DEMO_PASSWORD, LOGIN_TIMEOUT_MS)
        ui.sendChatMessage("昨晚这笔境外消费不是我的")
        try {
            ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
                ui.onAllNodesWithTag(UiTags.APPROVAL_APPROVE).fetchSemanticsNodes().isNotEmpty()
            }
        } catch (timeout: ComposeTimeoutException) {
            ui.onRoot().printToLog("ANTIFRAUD")
            throw timeout
        }
        assertTrue(ui.onAllNodesWithText("境外", substring = true).fetchSemanticsNodes().isNotEmpty())
        assertTrue(ui.onAllNodesWithText("红色", substring = true).fetchSemanticsNodes().isNotEmpty())
        assertTrue(ui.onAllNodesWithTag(UiTags.APPROVAL_OTP).fetchSemanticsNodes().isNotEmpty())
        ui.onNodeWithTag(UiTags.APPROVAL_OTP).performTextInput(OTP)
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performClick()
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            ui.onAllNodesWithTag(UiTags.APPROVAL_APPROVE).fetchSemanticsNodes().isEmpty()
        }
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            val frozen = ui.onAllNodesWithText("冻结", substring = true).fetchSemanticsNodes().isNotEmpty()
            val human = ui.onAllNodesWithText("人工审核", substring = true).fetchSemanticsNodes().isNotEmpty()
            val noRefund = ui.onAllNodesWithText("未执行退款", substring = true).fetchSemanticsNodes().isNotEmpty()
            frozen && human && noRefund
        }
    }

    private companion object {
        const val DEMO_PASSWORD = "Bank@2026"
        const val OTP = "888888"
        const val LOGIN_TIMEOUT_MS = 30_000L
        const val TURN_TIMEOUT_MS = 60_000L
    }
}
