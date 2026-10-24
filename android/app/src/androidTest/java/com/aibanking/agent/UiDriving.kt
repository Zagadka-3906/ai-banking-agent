package com.aibanking.agent

import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.junit4.ComposeContentTestRule
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextReplacement
import androidx.test.espresso.Espresso
import com.aibanking.agent.ui.UiTags

/**
 * Shared UI driving for the instrumented tests.
 *
 * Extracted after a duplicated copy in `RedTierUiTest` drifted: a fix to the
 * "the click was swallowed" workaround landed in one class only, and the other
 * silently kept failing with a timeout that pointed at the wrong thing.  One
 * implementation, one behaviour.
 *
 * Tests create their environment through the v2 factories. ComposeContentTestRule
 * remains the shared (non-deprecated) interface in junit4, including for v2.
 * waitForIdle/semantics operations drain queued composition work; waitUntil also
 * yields to real network and Keystore work, which is outside the virtual clock.
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
        onNodeWithTag(UiTags.LOGIN_PASSWORD).performScrollTo().performTextInput(password)
        submitLoginForm()
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

/** Submit after typing, once the system keyboard can no longer move the button. */
internal fun ComposeContentTestRule.submitLoginForm() {
    // Compose idle alone does not wait for the system IME's inset animation.
    Espresso.closeSoftKeyboard()
    waitForIdle()
    onNodeWithTag(UiTags.LOGIN_BUTTON).performScrollTo().assertIsDisplayed().assertIsEnabled().performClick()
}

/**
 * Type and send one instruction, and verify the app actually took it.
 *
 * A synthetic click is occasionally swallowed while a system window momentarily
 * holds focus.  Without this check that shows up much later as "the approval
 * card never appeared", which points at the wrong thing entirely.
 */
internal fun ComposeContentTestRule.sendChatMessage(text: String) {
    returnFromOutcome(settleMs = 200)
    waitForIdle()
    repeat(3) {
        val typed = runCatching {
            waitForIdle()
            onNodeWithTag(UiTags.CHAT_INPUT).performTextInput(text)
        }.isSuccess
        if (typed) {
            repeat(3) {
                onNodeWithTag(UiTags.CHAT_SEND).performClick()
                if (runCatching { waitUntil(timeoutMillis = 5_000) { inputCleared() } }.isSuccess) return
            }
        }
    }
    throw AssertionError("the app never accepted the message; the input field still holds it")
}

/** Whether the chat field is empty, i.e. the app accepted the last message. */
private fun ComposeContentTestRule.inputCleared(): Boolean {
    val node = onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().firstOrNull() ?: return false
    return node.config.getOrNull(SemanticsProperties.EditableText)?.text.isNullOrEmpty()
}

/**
 * Leave a usable code in the red approval's SMS field.
 *
 * The debug build fetches the issued demo code and fills it in on its own, and
 * once a code has been issued the core accepts only that one - so overwriting a
 * filled field guarantees a rejection. The isolated scenario build leaves the
 * field empty, where the fixed test code is what the core expects.
 */
internal fun ComposeContentTestRule.enterApprovalOtp(fallback: String, timeoutMs: Long = 10_000) {
    waitUntil(timeoutMs) { onAllNodesWithTag(UiTags.APPROVAL_OTP).fetchSemanticsNodes().isNotEmpty() }
    if (BuildConfig.AUTO_FILL_DEMO_OTP) {
        // Debug acceptance must prove automatic issuance and filling. Falling
        // back to a fixed code would hide a broken demo OTP request.
        waitUntil(timeoutMs) { approvalOtpText().isNotBlank() }
    } else {
        onNodeWithTag(UiTags.APPROVAL_OTP).performScrollTo().performTextReplacement(fallback)
    }
}

private fun ComposeContentTestRule.approvalOtpText(): String =
    onAllNodesWithTag(UiTags.APPROVAL_OTP).fetchSemanticsNodes().firstOrNull()
        ?.config?.getOrNull(SemanticsProperties.EditableText)?.text.orEmpty()

/**
 * The text currently in the chat field, or null when the chat screen is not shown.
 *
 * Panel actions do not send anything by themselves: they prefill the field and
 * return to the conversation, so the prefill is the observable result of a click.
 */
internal fun ComposeContentTestRule.chatInputText(): String? =
    onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().firstOrNull()
        ?.config?.getOrNull(SemanticsProperties.EditableText)?.text

/**
 * A completed approval opens the result page on its own; go back to the
 * conversation if it is showing. Waits briefly so a page about to open is not missed.
 */
internal fun ComposeContentTestRule.returnFromOutcome(settleMs: Long = 1_500) {
    runCatching { waitUntil(settleMs) { onAllNodesWithTag(UiTags.OUTCOME_RETURN).fetchSemanticsNodes().isNotEmpty() } }
    if (onAllNodesWithTag(UiTags.OUTCOME_RETURN).fetchSemanticsNodes().isNotEmpty()) {
        onNodeWithTag(UiTags.OUTCOME_RETURN).performClick()
        waitUntil(10_000) { onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().isNotEmpty() }
    }
}

/**
 * Open a destination from the side drawer (cards, wealth, contacts, tasks,
 * devices, logout). From a full-screen page the drawer button is not shown, so
 * go back to the conversation first, as a user would.
 */
internal fun ComposeContentTestRule.openFromDrawer(tag: String, timeoutMs: Long = 10_000) {
    // A detail page (wealth product, contact) needs two steps back to the conversation.
    repeat(3) {
        if (onAllNodesWithTag(UiTags.OPEN_DRAWER).fetchSemanticsNodes().isEmpty()) {
            Espresso.pressBack()
            waitForIdle()
        }
    }
    waitUntil(timeoutMs) { onAllNodesWithTag(UiTags.OPEN_DRAWER).fetchSemanticsNodes().isNotEmpty() }
    onNodeWithTag(UiTags.OPEN_DRAWER).performClick()
    waitForIdle()
    onNodeWithTag(tag).assertIsDisplayed().performClick()
    waitForIdle()
}
