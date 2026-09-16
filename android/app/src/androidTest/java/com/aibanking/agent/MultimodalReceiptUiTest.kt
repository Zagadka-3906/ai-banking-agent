package com.aibanking.agent

import android.Manifest
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import com.aibanking.agent.ui.UiTags
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** 三种小型 Mock 媒体均走真实网关上传；不宣称真实 OCR/ASR。 */
@RunWith(AndroidJUnit4::class)
class MultimodalReceiptUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()
    @get:Rule val notifications: GrantPermissionRule = GrantPermissionRule.grant(Manifest.permission.POST_NOTIFICATIONS)

    @Test
    fun all_mock_media_create_drafts_without_debit() {
        ui.ensureLoggedIn("Bank@2026", 30_000L)

        ui.onNodeWithTag(UiTags.MOCK_INVOICE).performClick()
        ui.waitUntil(60_000L) { ui.onAllNodesWithText("Mock 京都酒店", substring = true).fetchSemanticsNodes().isNotEmpty() }
        assertTrue(ui.onAllNodesWithText("MOCK_RULES", substring = true).fetchSemanticsNodes().isNotEmpty())
        assertTrue(ui.onAllNodesWithText("置信度：99%", substring = true).fetchSemanticsNodes().isNotEmpty())
        assertTrue(ui.onAllNodesWithText("DRAFT", substring = true).fetchSemanticsNodes().isNotEmpty())
        assertTrue(ui.onAllNodesWithText("未扣款", substring = true).fetchSemanticsNodes().isNotEmpty())

        ui.onNodeWithTag(UiTags.MOCK_BILL).performClick()
        ui.waitUntil(60_000L) { ui.onAllNodesWithText("Mock 餐厅", substring = true).fetchSemanticsNodes().isNotEmpty() }
        assertTrue(ui.onAllNodesWithText("置信度：98%", substring = true).fetchSemanticsNodes().isNotEmpty())

        ui.onNodeWithTag(UiTags.MOCK_VOICE).performClick()
        ui.waitUntil(60_000L) { ui.onAllNodesWithText("Mock 机场快线", substring = true).fetchSemanticsNodes().isNotEmpty() }
        assertTrue(ui.onAllNodesWithText("置信度：97%", substring = true).fetchSemanticsNodes().isNotEmpty())
        assertTrue(ui.onAllNodesWithText("未扣款", substring = true).fetchSemanticsNodes().isNotEmpty())
    }
}
