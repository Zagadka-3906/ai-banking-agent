package com.aibanking.agent.ui.chat

import com.aibanking.agent.data.CardView
import com.aibanking.agent.data.GatewayException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.security.MessageDigest

// ChatViewModel actions for the bank-card panel.

// -- bank cards (银行卡管理) ----------------------------------------------

fun ChatViewModel.openCards() {
    _state.update { it.copy(showCards = true, showWealth = false, showContacts = false, showDevices = false, showTasks = false, panelBusy = true) }
    refreshCards()
}

fun ChatViewModel.closeCards() = _state.update { it.copy(showCards = false) }

fun ChatViewModel.refreshCards() {
    val principal = _state.value.principal
    sessionWork.launch {
        try {
            val rows = io { cardsRepo.list(principal, credential().orEmpty()) }
            _state.update { it.copy(cards = rows, panelBusy = false, panelError = null) }
        } catch (failure: GatewayException) {
            check()
            applyPanelFailure(failure)
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            applyPanelFailure(failure)
        }
    }
}

fun ChatViewModel.onCardFormChange(cardType: String? = null, label: String? = null) {
    _state.update { it.copy(cardType = cardType ?: it.cardType, cardLabel = label?.take(40) ?: it.cardLabel) }
}

/** 自行绑定银行卡：黄色操作，需用户明确确认并计入审计。 */
fun ChatViewModel.addCard(cardNumber: String, source: String) {
    val principal = _state.value.principal
    val form = _state.value
    if (form.panelBusy) return
    // 同一次提交的所有重试共用一个幂等键（见 [CreateAttempt]）：20 秒读超时后
    // 重发不会开出第二张卡。表单内容变了才是另一次提交，那时必须换新键。
    val digest = MessageDigest.getInstance("SHA-256").digest("${form.cardType}\u0000${form.cardLabel.trim()}\u0000$cardNumber".toByteArray()).joinToString("") { "%02x".format(it) }
    val attempt = CreateAttempt.of(cardCreateAttempt, digest)
    cardCreateAttempt = attempt
    _state.update { it.copy(panelBusy = true, error = null) }
    sessionWork.launch {
        try {
            val created = io {
                cardsRepo.add(principal, credential().orEmpty(), form.cardType, form.cardLabel, attempt.value, cardNumber, source)
            }
            // 卡已经开出：这次提交到此为止，下一次添加是新的意图、新的键。
            cardCreateAttempt = null
            _state.update {
                it.copy(
                    panelBusy = false,
                    cardLabel = "",
                    // 核心已经把新卡返回来了，就立刻放进列表：添加表单据此判断
                    // “真的开出来了”才收起，而后续的列表刷新即使失败，卡也不会
                    // 从页面上消失（它确实已经存在）。
                    cards = it.cards + created,
                    notice = "银行卡尾号 ${created.last4.ifBlank { created.numberMasked.takeLast(4) }} 已添加",
                )
            }
            refreshCards()
        } catch (failure: GatewayException) {
            check()
            applyPanelFailure(failure)
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            // 失败必须收尾：panelBusy 留在 true 会让添加表单永远停在“开卡中…”。
            applyPanelFailure(failure)
        }
    }
}

/** PIN stays in the detail composable; this method never stores the reveal result. */
suspend fun ChatViewModel.revealCard(cardId: String, pin: String): com.aibanking.agent.data.CardRevealView {
    val owner = sessionWork.capture()
    val principal = _state.value.principal
    return owner.io { cardsRepo.reveal(principal, owner.credential().orEmpty(), cardId, pin) }
}

fun ChatViewModel.prefillCardTransfer(card: CardView) {
    onInputChange("使用卡片 ${card.cardId}（尾号${card.last4.ifBlank { card.numberMasked.takeLast(4) }}）转账")
    closeCards()
}

/** 冻结/挂失/解挂等红色操作：预填话术回对话，走短信+设备签名审批。 */
fun ChatViewModel.startCardAction(action: String, card: CardView) {
    val phrase = when (action) {
        "freeze" -> "冻结卡片 ${card.cardId}"
        "unfreeze" -> "解冻卡片 ${card.cardId}"
        "report_lost" -> "挂失卡片 ${card.cardId}"
        // 解挂是 LOST 的唯一出口（核心 CARD_TRANSITIONS: LOST → ACTIVE）；
        // “解挂”也是 Agent 识别 CARD_UNREPORT_LOST 的关键词。
        "unreport_lost" -> "解挂卡片 ${card.cardId}"
        else -> return
    }
    onInputChange(phrase)
    closeCards()
}
