package com.aibanking.agent

import android.Manifest
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import com.aibanking.agent.ui.UiTags
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** 旅行模式端到端演示：真实中文输入、红区审批、OTP 和设备生物识别。 */
@RunWith(AndroidJUnit4::class)
class TravelModeUiTest {
    @get:Rule
    val ui = createAndroidComposeRule<MainActivity>()

    @get:Rule
    val notifications: GrantPermissionRule = GrantPermissionRule.grant(Manifest.permission.POST_NOTIFICATIONS)

    @Test
    fun travel_mode_shows_mock_preview_and_restoration_plan() {
        ui.ensureLoggedIn("Bank@2026", TIMEOUT_MS)
        ui.sendChatMessage("开启日本旅行模式，2027-01-10到2027-01-15，预算8000元，境外限额10000元")

        ui.waitUntil(TIMEOUT_MS) { ui.onAllNodesWithTag(UiTags.APPROVAL_APPROVE).fetchSemanticsNodes().isNotEmpty() }
        val approval = ui.onAllNodesWithText("日本", substring = true).fetchSemanticsNodes().isNotEmpty() &&
            ui.onAllNodesWithText("MOCK", substring = true).fetchSemanticsNodes().isNotEmpty() &&
            ui.onAllNodesWithText("10,000", substring = true).fetchSemanticsNodes().isNotEmpty()
        assertTrue("红区审批未展示日本、MOCK汇率和临时限额", approval)
        assertTrue("旅行模式未进入红区审批", ui.onAllNodesWithText("红色", substring = true).fetchSemanticsNodes().isNotEmpty() || ui.onAllNodesWithText("强验证", substring = true).fetchSemanticsNodes().isNotEmpty())

        ui.onNodeWithTag(UiTags.APPROVAL_OTP).performTextInput("888888")
        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performClick()

        // 生物识别系统窗由宿主机 satisfy-biometric-prompt.ps1 完成；这里等待真实
        // Core 结果，而不是用 sleep 假造完成。
        ui.waitUntil(TIMEOUT_MS) { ui.onAllNodesWithText("旅行模式已开启", substring = true).fetchSemanticsNodes().isNotEmpty() }
        val finalText = ui.onAllNodesWithText("旅行模式已开启", substring = true).fetchSemanticsNodes().isNotEmpty() &&
            ui.onAllNodesWithText("自动恢复", substring = true).fetchSemanticsNodes().isNotEmpty() &&
            ui.onAllNodesWithText("MOCK", substring = true).fetchSemanticsNodes().isNotEmpty() &&
            ui.onAllNodesWithText("未扣款", substring = true).fetchSemanticsNodes().isNotEmpty()
        assertTrue("旅行模式最终结果缺少自动恢复、Mock或未扣款边界", finalText)
    }

    private companion object { const val TIMEOUT_MS = 60_000L }
}
