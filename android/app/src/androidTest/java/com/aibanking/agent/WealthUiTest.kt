package com.aibanking.agent

import android.Manifest
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import com.aibanking.agent.ui.UiTags
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.rules.RuleChain
import org.junit.rules.TestRule

@RunWith(AndroidJUnit4::class)
class WealthUiTest {
    private val notifications: GrantPermissionRule = GrantPermissionRule.grant(Manifest.permission.POST_NOTIFICATIONS)
    private val ui = createAndroidComposeRule<MainActivity>()

    @get:Rule
    val rules: TestRule = RuleChain.outerRule(notifications).around(ui)

    @Test
    fun product_detail_shows_mock_data_and_risk_test_boundary() {
        ui.ensureLoggedIn("Bank@2026")
        ui.onNodeWithTag(UiTags.OPEN_WEALTH).performClick()
        ui.waitUntil(45_000) {
            ui.onAllNodesWithTag(UiTags.wealthProduct("CJ01002")).fetchSemanticsNodes().isNotEmpty()
        }
        ui.onNodeWithTag(UiTags.wealthProduct("CJ01002")).performClick()
        listOf(UiTags.WEALTH_DETAIL, UiTags.WEALTH_SNAPSHOT, UiTags.WEALTH_CHART, UiTags.WEALTH_HISTORY, UiTags.WEALTH_RULES, UiTags.WEALTH_DISCLAIMER).forEach { tag ->
            assertTrue("缺少 UI 标签 $tag", ui.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty())
        }
        assertTrue(ui.onAllNodesWithText("Mock", substring = true).fetchSemanticsNodes().isNotEmpty())
        assertTrue(ui.onAllNodesWithText("2026-09-15", substring = true).fetchSemanticsNodes().isNotEmpty())

        ui.onNodeWithTag(UiTags.wealthRange("近1月")).performClick()
        ui.waitUntil(5_000) {
            ui.onAllNodesWithText("● 近1月", substring = true).fetchSemanticsNodes().isNotEmpty()
        }
        ui.onNodeWithTag(UiTags.WEALTH_TO_TEST).performClick()
        ui.waitUntil(10_000) {
            ui.onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().isNotEmpty()
        }
        assertTrue(ui.chatInputText()?.contains("我想做风险测评") == true)
    }
}
