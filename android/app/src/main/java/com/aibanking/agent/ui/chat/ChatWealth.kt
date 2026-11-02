package com.aibanking.agent.ui.chat

import com.aibanking.agent.data.GatewayException
import com.aibanking.agent.data.ProductView
import com.google.gson.Gson
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.math.BigDecimal

// ChatViewModel actions for the wealth pages and the 8-question risk quiz.

// -- wealth (银行风格理财页) ----------------------------------------------

fun ChatViewModel.openWealth() {
    _state.update { it.copy(showWealth = true, showCards = false, showContacts = false, showDevices = false, showTasks = false, wealthScreen = WealthScreen.LIST, wealthMarketGroupId = null, panelBusy = true) }
    refreshWealth()
}

fun ChatViewModel.closeWealth() = _state.update { it.copy(showWealth = false) }

fun ChatViewModel.selectWealthMarketGroup(groupId: String?) =
    _state.update { it.copy(wealthMarketGroupId = groupId) }

fun ChatViewModel.openWealthProduct(productId: String) {
    openWealth()
    showProductDetail(productId)
}

fun ChatViewModel.showProductDetail(productId: String) =
    _state.update { it.copy(wealthScreen = WealthScreen.DETAIL, selectedProductId = productId, buyAmount = "", purchaseAmountError = null) }

fun ChatViewModel.backToWealthList() =
    _state.update { it.copy(wealthScreen = WealthScreen.LIST, selectedProductId = null, buyAmount = "", purchaseAmountError = null) }

/**
 * 申购金额只接受合法十进制文本：最多一个小数点、小数最多两位。
 *
 * 旧实现只过滤“非数字且非点”的字符，于是 `1.2.3` 也能进输入框，到
 * [startPurchase] 里要么被 `toDoubleOrNull` 判成 null（点了没反应），
 * 要么经 `Double.toString()` 变成科学计数法发给核心。非法编辑直接不采纳
 * （保留上一次的合法文本），而不是先塞进状态再在提交时失败。
 */
fun ChatViewModel.onBuyAmountChange(value: String) =
    _state.update {
        if (AMOUNT_INPUT.matches(value)) {
            it.copy(buyAmount = value, purchaseAmountError = null)
        } else {
            it.copy(purchaseAmountError = "请输入合法金额，最多两位小数")
        }
    }

fun ChatViewModel.refreshWealth() {
    val principal = _state.value.principal
    sessionWork.launch {
        try {
            val products = io { wealth.products(principal, credential().orEmpty()) }
            val holdings = io { wealth.holdings(principal, credential().orEmpty()) }
            val profile = io { wealth.riskProfile(principal, credential().orEmpty()) }
            _state.update { it.copy(wealthProducts = products, wealthHoldings = holdings, riskProfile = profile, panelBusy = false, panelError = null) }
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

/**
 * 申购不在页面里发生：预填聊天话术并回到对话，金额与产品代码由 Agent
 * 重新解析、核心按红色风险流程（短信 + 设备签名）最终授权。客户端只是
 * 把用户的意图送到那条安全链路的入口。
 */
fun ChatViewModel.startPurchase(product: ProductView, amountText: String) {
    val amount = parseAmountInput(amountText) ?: return
    if (amount.signum() <= 0 || amount < BigDecimal.valueOf(product.minAmount)) return
    // toPlainString：`Double.toString()` 会把大额写成 1.0E11 这种科学计数法，
    // 那不是银行话术，也不是核心能解析的金额。
    onInputChange("申购 ${product.productId} ${amount.toPlainString()} 元")
    closeWealth()
}

/** 点选一个选项：记录并推进；答完第 8 题自动汇成一条消息提交。 */
fun ChatViewModel.onQuizAnswer(question: QuizQuestionUi, key: String) {
    markUserActivity()
    val quiz = _state.value.quiz ?: return
    if (question.number != quiz.currentQuestion?.number) return
    val answers = quiz.answers + (question.number to key)
    if (answers.size < quiz.questions.size) {
        _state.update { it.copy(quiz = quiz.copy(answers = answers)) }
        persistQuiz(_state.value.quiz)
        return
    }
    val combined = quiz.questions.mapNotNull { it.number.toString() + answers[it.number] }
        .joinToString(" ")
    _state.update { it.copy(quiz = null, input = "") }
    persistQuiz(null)
    // 答案只在后台存档：不显示作答消息，直接呈现测评结果。
    sendInternal(combined, echoUser = false)
}

/** 放弃测评：把放弃意图发出去，让服务端把问卷状态一并关闭。 */
fun ChatViewModel.giveUpQuiz() {
    if (_state.value.quiz == null) return
    _state.update { it.copy(quiz = null) }
    persistQuiz(null)
    sendInternal("退出测评")
}

/**
 * 问卷组件状态随手持久化：题目与已作答进度一起存，重装/重启后接着答，
 * 不困在文字兜底里，也不会把答了 5/8 的用户退回第一题。
 */
internal fun ChatViewModel.persistQuiz(quiz: RiskQuizUi?) {
    sessionWork.persist("quiz") {
        if (quiz == null) {
            sessionStore.clearQuiz()
        } else {
            runCatching { gson.toJson(quiz) }.getOrNull()?.let { sessionStore.saveQuiz(it) }
        }
    }
}

/**
 * 从存档恢复未答完的问卷（题目 + 进度）。
 *
 * 旧版本只存题目数组：那种载荷按 [RiskQuizUi] 解析会失败，这里退回按题目
 * 数组解析，进度从第一题重新开始——总好过把整个组件丢掉。
 */
internal fun ChatViewModel.parseStoredQuiz(json: String?): RiskQuizUi? {
    if (json == null) return null
    return runCatching {
        val saved = runCatching { gson.fromJson(json, RiskQuizUi::class.java) }.getOrNull()
            ?: RiskQuizUi(gson.fromJson(json, Array<QuizQuestionUi>::class.java).toList())
        // Gson 直接写字段、不走构造器，缺字段时数据类的默认值并不生效，
        // 所以这里按“可能是 null”处理，坏存档最多让组件不出现，不能崩启动。
        val questions = saved.questions.orEmpty()
        if (questions.size != 8) return@runCatching null
        // 只保留仍然存在的问题号，且必须是未答满的：答满的问卷已提交并清除。
        val numbers = questions.map { it.number }.toSet()
        val answers = saved.answers.orEmpty().filterKeys { it in numbers }
        if (answers.size >= questions.size) return@runCatching null
        RiskQuizUi(questions, answers)
    }.getOrNull()
}

internal fun ChatViewModel.parseQuiz(result: Map<String, Any>?): RiskQuizUi? {
    val payload = result?.get("risk_quiz") as? Map<*, *> ?: return null
    if (payload["active"] != true) return null
    val questions = (payload["questions"] as? List<*>)?.mapNotNull { q ->
        val m = q as? Map<*, *> ?: return@mapNotNull null
        val number = (m["number"] as? Number)?.toInt() ?: return@mapNotNull null
        val text = m["text"]?.toString() ?: return@mapNotNull null
        val options = (m["options"] as? List<*>)?.mapNotNull { option ->
            val om = option as? Map<*, *> ?: return@mapNotNull null
            val key = om["key"]?.toString() ?: return@mapNotNull null
            QuizOptionUi(key, om["label"]?.toString() ?: "")
        } ?: emptyList()
        if (options.isEmpty()) null else QuizQuestionUi(number, text, options)
    } ?: emptyList()
    if (questions.size != 8) return null
    return RiskQuizUi(questions)
}
