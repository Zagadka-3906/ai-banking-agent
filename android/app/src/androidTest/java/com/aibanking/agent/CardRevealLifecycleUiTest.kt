package com.aibanking.agent

import androidx.activity.ComponentActivity
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.lifecycle.Lifecycle
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.data.CardRevealView
import com.aibanking.agent.data.CardView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.CardDetail
import com.aibanking.agent.ui.theme.BankAgentTheme
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.Continuation
import kotlin.coroutines.suspendCoroutine
import kotlinx.coroutines.CancellationException
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Real Activity stop/resume with responses that deliberately ignore coroutine cancellation. */
@RunWith(AndroidJUnit4::class)
@BankingRegression
class CardRevealLifecycleUiTest {
    @get:Rule val ui = createAndroidComposeRule<ComponentActivity>()
    private val heldResponses = mutableListOf<HeldResponses>()

    @After fun releasePendingResponses() {
        // A failed assertion must not leave our non-cancellable fixture suspended.
        ui.runOnUiThread { heldResponses.forEach { it.releaseRemaining() } }
        ui.mainClock.advanceTimeByFrame()
    }

    @Test fun a_response_received_in_background_stays_hidden_after_resuming() {
        val responses = render()
        requestReveal()
        ui.activityRule.scenario.moveToState(Lifecycle.State.CREATED)

        deliver(responses, 0, Result.success(revealed("background")))
        ui.activityRule.scenario.moveToState(Lifecycle.State.RESUMED)

        assertHidden()
        val input = ui.onNodeWithTag(UiTags.CARD_PIN).assertIsEnabled().fetchSemanticsNode()
        assertEquals("", input.config[SemanticsProperties.EditableText].text)
        ui.onNodeWithTag(UiTags.CARD_REVEAL).assertIsNotEnabled()
    }

    @Test fun an_old_success_after_resuming_cannot_replace_or_finish_a_new_request() {
        val responses = render()
        requestReveal()
        ui.activityRule.scenario.moveToState(Lifecycle.State.CREATED)
        ui.activityRule.scenario.moveToState(Lifecycle.State.RESUMED)
        requestReveal()

        deliver(responses, 0, Result.success(revealed("old-success")))

        assertHidden()
        ui.onNodeWithTag(UiTags.CARD_PIN).assertIsNotEnabled()
        ui.onNodeWithTag(UiTags.CARD_REVEAL).assertIsNotEnabled()
        deliver(responses, 1, Result.success(revealed("fresh-success")))
        ui.onNodeWithText("卡号：$CARD_NUMBER").assertExists()
    }

    @Test fun an_old_error_after_resuming_cannot_replace_or_finish_a_new_request() {
        val responses = render()
        requestReveal()
        ui.activityRule.scenario.moveToState(Lifecycle.State.CREATED)
        ui.activityRule.scenario.moveToState(Lifecycle.State.RESUMED)
        requestReveal()

        deliver(responses, 0, Result.failure(IllegalStateException("delayed failure")))

        assertHidden()
        ui.onNodeWithText("验证失败，请检查取款密码").assertDoesNotExist()
        ui.onNodeWithTag(UiTags.CARD_PIN).assertIsNotEnabled()
        ui.onNodeWithTag(UiTags.CARD_REVEAL).assertIsNotEnabled()
        deliver(responses, 1, Result.success(revealed("fresh-after-error")))
        ui.onNodeWithText("卡号：$CARD_NUMBER").assertExists()
    }

    private fun render(): HeldResponses {
        val responses = HeldResponses().also { heldResponses += it }
        ui.setContent {
            BankAgentTheme {
                CardDetail(
                    card = CardView(cardId = "CARD-LIFECYCLE", numberMasked = "**** 3082", type = "借记卡"),
                    revealCard = responses::request,
                    onTransfer = {}, onWealth = {}, onBack = {},
                )
            }
        }
        return responses
    }

    private fun requestReveal() {
        ui.onNodeWithTag(UiTags.CARD_PIN).performScrollTo().performTextInput("24681012")
        androidx.test.espresso.Espresso.closeSoftKeyboard()
        ui.waitForIdle()
        ui.onNodeWithTag(UiTags.CARD_REVEAL).performScrollTo().performClick()
        ui.waitForIdle()
        ui.onNodeWithTag(UiTags.CARD_PIN).assertIsNotEnabled()
    }

    private fun deliver(responses: HeldResponses, index: Int, result: Result<CardRevealView>) {
        // Do not use runOnIdle while the Activity is stopped: the deliberately
        // late callback must execute before we resume its visible composition.
        ui.runOnUiThread { responses.deliver(index, result) }
        ui.mainClock.advanceTimeByFrame()
        InstrumentationRegistry.getInstrumentation().waitForIdleSync()
        assertEquals("The delayed request must actually return", index + 1, responses.completed.get())
    }

    private fun assertHidden() {
        ui.onNodeWithTag(UiTags.CARD_REVEALED).assertDoesNotExist()
        ui.onNodeWithText("卡号：$CARD_NUMBER").assertDoesNotExist()
    }

    private fun revealed(id: String) = CardRevealView(
        ownerName = "测试账户", cardNumber = CARD_NUMBER, issuerName = "测试银行",
        expiresAt = "2099-01-01T00:00:00Z", disclosureId = id,
    )

    private class HeldResponses {
        private val pending = mutableListOf<Continuation<CardRevealView>>()
        private val delivered = mutableSetOf<Int>()
        val completed = AtomicInteger()

        fun deliver(index: Int, result: Result<CardRevealView>) {
            check(delivered.add(index)) { "Response already delivered: $index" }
            pending[index].resumeWith(result)
        }

        fun releaseRemaining() {
            pending.indices.filter { it !in delivered }.forEach { index ->
                deliver(index, Result.failure(CancellationException("Test finished")))
            }
        }

        suspend fun request(cardId: String, pin: String): CardRevealView {
            assertEquals("CARD-LIFECYCLE", cardId)
            assertEquals("24681012", pin)
            try {
                // suspendCoroutine deliberately ignores Job cancellation. This
                // proves stale results are rejected even if transport cannot stop.
                return suspendCoroutine { pending += it }
            } finally {
                completed.incrementAndGet()
            }
        }
    }

    private companion object {
        const val CARD_NUMBER = "6222020000023082"
    }
}
