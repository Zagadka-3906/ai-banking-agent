package com.aibanking.agent

import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.junit4.ComposeContentTestRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import com.aibanking.agent.ui.UiTags

/**
 * Shared UI driving for the instrumented tests.
 *
 * Extracted after a duplicated copy in `RedTierUiTest` drifted: a fix to the
 * "the click was swallowed" workaround landed in one class only, and the other
 * silently kept failing with a timeout that pointed at the wrong thing.  One
 * implementation, one behaviour.
 */

/** Log in unless a session was already restored from storage. */
internal fun ComposeContentTestRule.ensureLoggedIn(password: String, timeoutMs: Long = 30_000) {
    // The stored session is unsealed on a background thread (Keystore work), so
    // the app spends its first moments on the restoring screen.  Wait for that to
    // resolve into either the login form or the chat screen before deciding, or a
    // logged-out launch looks like "already logged in" and the test then waits
    // forever for a chat field that is never coming.
    waitUntil(timeoutMs) {
        onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().isNotEmpty() ||
            onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().isNotEmpty()
    }
    if (onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().isNotEmpty()) {
        onNodeWithTag(UiTags.LOGIN_PASSWORD).performTextInput(password)
        onNodeWithTag(UiTags.LOGIN_BUTTON).performClick()
    }
    waitUntil(timeoutMs) {
        // Exactly one: a leftover Activity from an earlier test in the same
        // process would leave a second Compose hierarchy behind, and the
        // semantics matchers search all of them - so a test could type into the
        // previous screen and then wait forever for a card that appears
        // somewhere it is not looking.
        onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().size == 1
    }
}

/**
 * Type and send one instruction, and verify the app actually took it.
 *
 * A synthetic click is occasionally swallowed while a system window momentarily
 * holds focus.  Without this check that shows up much later as "the approval
 * card never appeared", which points at the wrong thing entirely.
 */
internal fun ComposeContentTestRule.sendChatMessage(text: String) {
    onNodeWithTag(UiTags.CHAT_INPUT).performTextInput(text)
    repeat(2) {
        onNodeWithTag(UiTags.CHAT_SEND).performClick()
        if (runCatching { waitUntil(timeoutMillis = 5_000) { inputCleared() } }.isSuccess) return
    }
    throw AssertionError("the app never accepted the message; the input field still holds it")
}

/** Whether the chat field is empty, i.e. the app accepted the last message. */
private fun ComposeContentTestRule.inputCleared(): Boolean {
    val node = onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().firstOrNull() ?: return false
    return node.config.getOrNull(SemanticsProperties.EditableText)?.text.isNullOrEmpty()
}

/**
 * The text currently in the chat field, or null when the chat screen is not shown.
 *
 * Panel actions do not send anything by themselves: they prefill the field and
 * return to the conversation, so the prefill is the observable result of a click.
 */
internal fun ComposeContentTestRule.chatInputText(): String? =
    onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().firstOrNull()
        ?.config?.getOrNull(SemanticsProperties.EditableText)?.text
