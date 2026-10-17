package com.aibanking.agent

import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onParent
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.hasTestTag
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.aibanking.agent.ui.UiTags
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
@BankingRegression
class WealthUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()

    @Test
    fun product_detail_shows_mock_data_and_risk_test_boundary() {
        ui.ensureLoggedIn("Bank@2026")
        ui.openFromDrawer(UiTags.OPEN_WEALTH)
        ui.waitUntil(45_000) {
            ui.onAllNodesWithTag(UiTags.WEALTH_LIST).fetchSemanticsNodes().isNotEmpty()
        }
        ui.onNodeWithTag(UiTags.WEALTH_LIST).performScrollToNode(hasTestTag(UiTags.wealthProduct("SG20270")))
        ui.onNodeWithTag(UiTags.wealthProduct("SG20270")).performClick()
        listOf(UiTags.WEALTH_DETAIL, UiTags.WEALTH_SUMMARY, UiTags.WEALTH_CHART, UiTags.WEALTH_HISTORY, UiTags.WEALTH_PROFILE, UiTags.WEALTH_MANAGER, UiTags.WEALTH_ALLOCATION, UiTags.WEALTH_DISCLOSURE, UiTags.WEALTH_RULES, UiTags.WEALTH_DISCLAIMER, UiTags.WEALTH_BOTTOM_BAR).forEach { tag ->
            assertTrue("缺少 UI 标签 $tag", ui.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty())
        }
        assertTrue(ui.onAllNodesWithText("Mock", substring = true).fetchSemanticsNodes().isNotEmpty())
        assertTrue(ui.onAllNodesWithText("2026-09-15", substring = true).fetchSemanticsNodes().isNotEmpty())
        assertTrue(ui.onAllNodesWithText("7.85%", substring = true).fetchSemanticsNodes().isNotEmpty())
        assertTrue(ui.onAllNodesWithText("10,000元起购", substring = true).fetchSemanticsNodes().isNotEmpty())

        listOf("近1月", "近3月", "近6月", "近1年", "成立以来").forEach { ui.onNodeWithTag(UiTags.wealthRange(it)).assertExists() }
        // 先把筛选行滚入详情视口，再在横向筛选行内定位目标区间。
        ui.onNodeWithTag(UiTags.wealthRange("近6月")).onParent().performScrollTo()
        ui.onNodeWithTag(UiTags.wealthRange("近6月")).performScrollTo().performClick()
        ui.waitUntil(5_000) {
            ui.onAllNodesWithText("● 近6月", substring = true).fetchSemanticsNodes().isNotEmpty()
        }
        ui.onNodeWithTag(UiTags.WEALTH_HISTORY_TOGGLE).performScrollTo().performClick()
        assertTrue(ui.onAllNodesWithText("成立以来", substring = true).fetchSemanticsNodes().isNotEmpty())
        ui.onNodeWithTag(UiTags.WEALTH_DISCLOSURE_RULES).performScrollTo().performClick()
        ui.waitUntil(5_000) {
            ui.onAllNodesWithTag(UiTags.WEALTH_DISCLOSURE_BODY).fetchSemanticsNodes().isNotEmpty()
        }
        assertTrue(ui.onAllNodesWithText("买入确认T+1", substring = true, useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty())
        ui.onNodeWithTag(UiTags.WEALTH_TO_TEST).performClick()
        ui.waitUntil(10_000) {
            ui.onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().isNotEmpty()
        }
        assertTrue(ui.chatInputText()?.contains("我想做风险测评") == true)
    }
}
