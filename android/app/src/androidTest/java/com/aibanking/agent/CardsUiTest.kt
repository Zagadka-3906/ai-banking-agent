package com.aibanking.agent

import android.app.Application
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performScrollToIndex
import androidx.compose.ui.test.performScrollTo
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.aibanking.agent.data.CardView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.CardsUi
import com.aibanking.agent.ui.chat.ChatUiState
import com.aibanking.agent.ui.chat.ChatViewModel
import com.aibanking.agent.ui.theme.BankAgentTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 银行卡管理页的 UI 测试。
 *
 * 这一页的操作按钮过去共用同一个 `CARD_ACTION` 标签：两张卡以上
 * `onNodeWithTag(CARD_ACTION)` 直接因为“命中多个节点”抛错，而点错卡的代价是
 * 对一个不是用户指定的卡片发起红色审批（核心按 card_id 执行）。这里断言每个
 * 按钮只指向它自己那张卡。
 *
 * 需要在宿主机上跑着银行栈（10.0.2.2:8000 与 8100，演示账户口令
 * ``Bank@2026``），和 [LoginAndChatUiTest] 一样是集成测试；绑定卡是黄色操作，
 * 不需要短信或设备签名。
 */
@RunWith(AndroidJUnit4::class)
class CardsUiTest {

    @get:Rule
    val ui = createAndroidComposeRule<MainActivity>()
    @Test
    fun an_added_card_appears_in_the_list() {
        openCardsAndWaitForRows()
        val before = awaitCardCount()
        val knownIds = cardActionButtons().map { it.cardId }.toSet()

        ui.onNodeWithTag(UiTags.CARD_OPEN_ADD).performClick()
        check(ui.onAllNodesWithTag(UiTags.CARD_CAMERA).fetchSemanticsNodes().isNotEmpty())
        // This case owns the 5555 fixture.  The second-card action case owns
        // 4111, so either JUnit execution order remains duplicate-free.
        ui.onNodeWithTag(UiTags.CARD_NUMBER).performScrollTo().performTextInput("5555555555554444")
        ui.onNodeWithTag(UiTags.CARD_CONFIRM).performScrollTo().performClick()
        ui.onNodeWithTag(UiTags.CARD_ADD).performScrollTo().performClick()

        // 顶部计数来自刷新后的卡片列表，说明新卡确实进了列表，而不是只在提示里。
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) { cardCount() == before + 1 }
        // ...并且卡面本身也在。列表是懒加载的且一屏只放得下一张卡面，滚动是“换一张
        // 渲染”而不是“多渲染一张”，所以这里断言出现了此前没有的 cardId，而不是数量变大。
        ui.onNodeWithTag(UiTags.CARD_LIST).performScrollToIndex(before)
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            cardActionButtons().any { it.cardId !in knownIds }
        }
    }

    @Test
    fun card_detail_masks_then_reveals_and_clears_on_back() {
        openCardsAndWaitForRows()
        check(ui.onAllNodesWithTag("${UiTags.CARD_DETAIL}:CARD-001").fetchSemanticsNodes().isNotEmpty())
        check(ui.onAllNodesWithText("尾号 3082").fetchSemanticsNodes().isNotEmpty())
        check(ui.onAllNodesWithText("**** **** **** 3082").fetchSemanticsNodes().isEmpty())
        ui.onNodeWithTag("${UiTags.CARD_DETAIL}:CARD-001").performClick()
        check(ui.onAllNodesWithText("6222020000023082").fetchSemanticsNodes().isEmpty())
        ui.onNodeWithTag(UiTags.CARD_PIN).performScrollTo().performTextInput("24681012")
        ui.onNodeWithTag(UiTags.CARD_REVEAL).performScrollTo().performClick()
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) { ui.onAllNodesWithText("户名：张三").fetchSemanticsNodes().isNotEmpty() }
        check(ui.onAllNodesWithText("户名：张三").fetchSemanticsNodes().isNotEmpty())
        check(ui.onAllNodesWithText("卡号：6222020000023082").fetchSemanticsNodes().isNotEmpty())
        check(ui.onAllNodesWithText("开户行：招商银行").fetchSemanticsNodes().isNotEmpty())
        ui.onNodeWithContentDescription("返回").performClick()
        ui.onNodeWithTag("${UiTags.CARD_DETAIL}:CARD-001").performClick()
        check(ui.onAllNodesWithText("6222020000023082").fetchSemanticsNodes().isEmpty())
        check(ui.onAllNodesWithTag(UiTags.CARD_REVEAL).fetchSemanticsNodes().isNotEmpty())
    }

    @Test
    fun wrong_pin_never_shows_sensitive_data() {
        openCardsAndWaitForRows()
        ui.onNodeWithTag("${UiTags.CARD_DETAIL}:CARD-001").performClick()
        ui.onNodeWithTag(UiTags.CARD_PIN).performScrollTo().performTextInput("00000000")
        ui.onNodeWithTag(UiTags.CARD_REVEAL).performScrollTo().performClick()
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) { ui.onAllNodesWithText("验证失败，请检查取款密码").fetchSemanticsNodes().isNotEmpty() }
        check(ui.onAllNodesWithText("6222020000023082").fetchSemanticsNodes().isEmpty())
    }

    @Test
    fun card_shortcuts_target_id_and_open_wealth() {
        openCardsAndWaitForRows()
        ui.onNodeWithTag("${UiTags.CARD_DETAIL}:CARD-001").performClick()
        ui.onNodeWithTag(UiTags.CARD_TRANSFER).performScrollTo().performClick()
        check(ui.chatInputText()?.contains("CARD-001") == true)
        check(ui.chatInputText()?.contains("6222020000023082") != true)
        ui.openFromDrawer(UiTags.OPEN_CARDS)
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) { ui.onAllNodesWithTag("${UiTags.CARD_DETAIL}:CARD-001").fetchSemanticsNodes().isNotEmpty() }
        ui.onNodeWithTag("${UiTags.CARD_DETAIL}:CARD-001").performClick()
        ui.onNodeWithTag(UiTags.CARD_WEALTH).performScrollTo().performClick()
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) { ui.onAllNodesWithTag(UiTags.WEALTH_LIST).fetchSemanticsNodes().isNotEmpty() }
    }

    @Test
    fun a_second_cards_action_targets_that_card() {
        openCardsAndWaitForRows()
        if (awaitCardCount() < 2) {
            // 绑定卡需明确确认，不需要红色审批；两张卡才谈得上“第二张”。
            ui.onNodeWithTag(UiTags.CARD_OPEN_ADD).performClick()
            ui.onNodeWithTag(UiTags.CARD_NUMBER).performScrollTo().performTextInput("4111111111111111")
            ui.onNodeWithTag(UiTags.CARD_CONFIRM).performScrollTo().performClick()
            ui.onNodeWithTag(UiTags.CARD_ADD).performScrollTo().performClick()
            ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) { (cardCount() ?: 0) >= 2 }
        }

        // 大卡面在小屏上逐张展示；按列表顺序滚动读取，不能把懒加载数量当成总卡数。
        ui.onNodeWithTag(UiTags.CARD_LIST).performScrollToIndex(0)
        val firstCard = cardActionButtons().first().cardId
        ui.onNodeWithTag(UiTags.CARD_LIST).performScrollToIndex(1)
        val secondActions = cardActionButtons().filter { it.cardId != firstCard }
        check(secondActions.isNotEmpty()) { "滚动后仍未找到第二张卡，无法验证操作指向" }
        val secondCard = secondActions.first().cardId
        val target = secondActions.firstOrNull { it.cardId == secondCard && it.action == "freeze" }
            ?: secondActions.first()
        val verb = checkNotNull(ACTION_VERBS[target.action]) { "未知的卡片操作 ${target.action}" }

        ui.onNodeWithTag(UiTags.cardAction(target.cardId, target.action)).performScrollTo().performClick()

        // 操作只预填话术并回到对话：话术里的卡号必须是第二张卡，不能是第一张
        // （点错卡等于对一个用户没指定的卡片发起红色审批，核心按 card_id 执行）。
        val phrase = checkNotNull(ui.chatInputText()) { "点击卡片操作后没有回到对话输入框" }
        assertEquals("第二张卡的操作话术指错了", "${verb}卡片 $secondCard", phrase)
        assertFalse("话术指向了第一张卡（$firstCard）：$phrase", phrase.contains(firstCard))
    }

    private fun openCardsAndWaitForRows() {
        ui.ensureLoggedIn(DEMO_PASSWORD, LOGIN_TIMEOUT_MS)
        // Retry once, and verify: a synthetic click is occasionally swallowed while
        // a system window momentarily holds focus.  Without the check that shows up
        // as "the cards page never loaded" and sends the reader hunting for a
        // loading bug - which is exactly what happened the first time this test ran
        // (the class's first case logs in first and failed; the second, running
        // against an already-settled screen, passed on the same helper).
        repeat(2) {
            ui.openFromDrawer(UiTags.OPEN_CARDS)
            if (runCatching { ui.waitUntil(timeoutMillis = CLICK_SETTLE_MS) { cardActionButtons().isNotEmpty() } }.isSuccess) return
        }
        // Out of retries: wait the full window so the failure names the real symptom.
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) { cardActionButtons().isNotEmpty() }
    }

    private fun awaitCardCount(): Int {
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) { cardCount() != null }
        return checkNotNull(cardCount()) { "卡片页始终没有显示卡片数量" }
    }

    /** 顶栏的“N 张”，加载中或不在卡片页时为 null。 */
    private fun cardCount(): Int? =
        ui.onAllNodesWithText(" 张", substring = true)
            .fetchSemanticsNodes()
            .mapNotNull { node ->
                node.config.getOrNull(SemanticsProperties.Text)?.joinToString("") { part -> part.text }
            }
            .firstNotNullOfOrNull { text -> text.trim().removeSuffix("张").trim().toIntOrNull() }

    /**
     * 屏幕上每一颗卡片操作按钮，按视觉顺序排列。
     *
     * 标签形如 `card_action:<cardId>:<action>`。分隔符用冒号而非下划线：动作名本身
     * 含下划线（`report_lost`），按下划线切会把 cardId 读成 `CARD-001_report`、
     * 动作读成 `lost`。
     */
    private fun cardActionButtons(): List<CardActionButton> {
        val prefix = "${UiTags.CARD_ACTION}:"
        return ui.onAllNodes(SemanticsMatcher.keyIsDefined(SemanticsProperties.TestTag))
            .fetchSemanticsNodes()
            .mapNotNull { node ->
                val tag = node.config.getOrNull(SemanticsProperties.TestTag) ?: return@mapNotNull null
                if (!tag.startsWith(prefix)) return@mapNotNull null
                val parts = tag.removePrefix(prefix).split(":")
                if (parts.size != 2 || parts.any { it.isEmpty() }) return@mapNotNull null
                CardActionButton(parts[0], parts[1], node.boundsInRoot.top)
            }
            .sortedBy { it.top }
    }

    private data class CardActionButton(val cardId: String, val action: String, val top: Float)

    private companion object {
        const val DEMO_PASSWORD = "Bank@2026"
        const val LOGIN_TIMEOUT_MS = 30_000L
        const val TURN_TIMEOUT_MS = 45_000L

        /** How long a click gets to prove it was accepted before it is repeated. */
        const val CLICK_SETTLE_MS = 5_000L

        /** 操作名 → 预填话术里的动词，与 ChatViewModel.startCardAction 一致。 */
        val ACTION_VERBS = mapOf(
            "freeze" to "冻结",
            "unfreeze" to "解冻",
            "report_lost" to "挂失",
            "unreport_lost" to "解挂",
        )
    }
}

/**
 * 卡片操作区的渲染测试，不需要后端。
 *
 * 用人工构造的状态直接渲染 [CardsUi]：状态机的边界（挂失卡只能解挂）不该只在
 * 真机上用手点才能验证，而 LOST 卡要靠红色审批流程才能造出来，走不到。
 */
@RunWith(AndroidJUnit4::class)
@BankingRegression
class CardsPanelActionsTest {

    @get:Rule
    val ui = createComposeRule()

    @Test
    fun a_lost_card_offers_unreport_lost_and_prefills_that_phrase() {
        val application = ApplicationProvider.getApplicationContext<Application>()
        val viewModel = ChatViewModel(application)
        val lost = CardView(cardId = "CARD-009", numberMasked = "**** **** **** 0009", type = "借记卡", status = "LOST")
        val active = CardView(cardId = "CARD-010", numberMasked = "**** **** **** 0010", type = "借记卡", status = "ACTIVE")
        ui.setContent {
            BankAgentTheme {
                CardsUi(ChatUiState(showCards = true, cards = listOf(lost, active)), viewModel)
            }
        }

        // 挂失卡只给“解挂”（核心 CARD_TRANSITIONS: LOST → ACTIVE）；旧版给的
        // “解冻”核心只对 FROZEN 卡接受，点下去必然被拒，是条死路。
        check(ui.onAllNodesWithTag(UiTags.cardAction("CARD-009", "unreport_lost")).fetchSemanticsNodes().isNotEmpty())
        check(ui.onAllNodesWithTag(UiTags.cardAction("CARD-009", "unfreeze")).fetchSemanticsNodes().isEmpty())
        // 每张卡、每个操作各自可寻址：两张卡三颗按钮，互不遮蔽。
        ui.onNodeWithTag(UiTags.CARD_LIST).performScrollToIndex(1)
        check(ui.onAllNodesWithTag(UiTags.cardAction("CARD-010", "freeze")).fetchSemanticsNodes().isNotEmpty())
        check(ui.onAllNodesWithTag(UiTags.cardAction("CARD-010", "report_lost")).fetchSemanticsNodes().isNotEmpty())

        ui.onNodeWithTag(UiTags.CARD_LIST).performScrollToIndex(0)
        ui.onNodeWithTag(UiTags.cardAction("CARD-009", "unreport_lost")).performScrollTo().performClick()

        assertEquals("解挂卡片 CARD-009", viewModel.state.value.input)
    }
}
