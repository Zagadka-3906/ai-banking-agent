package com.aibanking.agent

import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.aibanking.agent.ui.UiTags
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ContactsUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()

    @Test
    fun wealth_and_cards_can_navigate_to_contacts() {
        ui.ensureLoggedIn("Bank@2026")

        ui.onNodeWithTag(UiTags.OPEN_WEALTH).performClick()
        ui.waitUntil(10_000) {
            ui.onAllNodesWithTag(UiTags.WEALTH_LIST).fetchSemanticsNodes().isNotEmpty()
        }
        ui.onNodeWithTag(UiTags.OPEN_CONTACTS).performClick()
        ui.waitUntil(10_000) {
            ui.onAllNodesWithTag(UiTags.CONTACT_SEARCH).fetchSemanticsNodes().isNotEmpty()
        }
        assertTrue(ui.onAllNodesWithTag(UiTags.CONTACT_SEARCH).fetchSemanticsNodes().isNotEmpty())

        ui.onNodeWithTag(UiTags.OPEN_CARDS).performClick()
        ui.waitUntil(10_000) {
            ui.onAllNodesWithTag(UiTags.CARD_LIST).fetchSemanticsNodes().isNotEmpty()
        }
        ui.onNodeWithTag(UiTags.OPEN_CONTACTS).performClick()
        ui.waitUntil(10_000) {
            ui.onAllNodesWithTag(UiTags.CONTACT_SEARCH).fetchSemanticsNodes().isNotEmpty()
        }
        assertTrue(ui.onAllNodesWithTag(UiTags.CONTACT_SEARCH).fetchSemanticsNodes().isNotEmpty())
    }
}
