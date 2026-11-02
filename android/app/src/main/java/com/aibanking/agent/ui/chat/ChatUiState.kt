package com.aibanking.agent.ui.chat

import com.aibanking.agent.data.CardView
import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.data.ContactView
import com.aibanking.agent.data.DeviceView
import com.aibanking.agent.data.HistoryRecordView
import com.aibanking.agent.data.HoldingView
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.data.RiskProfileView
import com.aibanking.agent.data.TaskView
import com.aibanking.agent.data.SplitRequestView
import com.aibanking.agent.data.ReplyResponse
import com.aibanking.agent.data.StageProgress
import com.aibanking.agent.ui.anim.RiskSignal
import com.aibanking.agent.ui.anim.RiskTier
import kotlinx.coroutines.launch
import java.math.BigDecimal
import java.time.LocalDate
import java.util.UUID

enum class ChatRole { USER, ASSISTANT }

/** 通讯录式联系人界面的三个页面：列表 → 详情 → 新增。 */
enum class ContactsScreen { LIST, DETAIL, ADD }

/** 理财页两个页面：产品超市 → 产品详情（申购预填回聊天）。 */
enum class WealthScreen { LIST, DETAIL }

internal data class DeviceApprovalCallbacks(
    val onSuccess: () -> Unit,
    val onFailure: (String) -> Unit,
)

internal val DEFAULT_PREDICTED_SUGGESTIONS = listOf(
    "查一下余额",
    "查看本月账单",
    "理财",
    "查看我的订阅",
)

data class ChatMessage(
    val id: String = UUID.randomUUID().toString(),
    val role: ChatRole,
    val text: String,
    /** Transient activity row replaced by the verified reply in the same list slot. */
    val streaming: Boolean = false,
    val riskLevel: String? = null,
    /** Temporary summary shown only while its approval is still actionable. */
    val approvalPreview: Boolean = false,
    /**
     * Structured result payload from the gateway (`chart_data`, `todo_card`,
     * `plan`, `execution_result`), kept so the result view can render facts
     * rather than only the prose summary.
     */
    val result: Map<String, Any>? = null,
    /**
     * The core's correlation id for this turn.  Shown to the user so a question
     * about a specific answer ("what happened with my transfer?") can be traced
     * through `/api/v1/security/audit/trace/{id}` instead of guessed at.
     */
    val traceId: String? = null,
    val businessStatus: String? = null,
    val response: ReplyResponse? = null,
    val conversationId: String? = null,
    val stages: List<StageProgress>? = null,
    val reconciling: Boolean = false,
    /** Failure to read a receipt does not mean the underlying operation failed. */
    val reconciliationError: String? = null,
)

data class QuizOptionUi(val key: String, val label: String)

data class QuizQuestionUi(val number: Int, val text: String, val options: List<QuizOptionUi>)

/** 逐题作答的问卷组件状态：answers.size 即当前题号。 */
data class RiskQuizUi(
    val questions: List<QuizQuestionUi>,
    val answers: Map<Int, String> = emptyMap(),
) {
    val answered: Int get() = answers.size
    val currentQuestion: QuizQuestionUi? get() = questions.getOrNull(answered)
}

data class ChatUiState(
    val principal: String = "",
    val authenticated: Boolean = false,
    /**
     * True while the sealed session is being unsealed off the main thread.
     *
     * Reading the credential means Keystore work (see the constructor), so it
     * cannot happen before the first frame.  The login form must not be shown
     * during that window: a logged-in app would flash a password prompt on every
     * launch.  MainActivity renders a plain loading screen instead.
     */
    val restoring: Boolean = false,
    val messages: List<ChatMessage> = emptyList(),
    val input: String = "",
    val busy: Boolean = false,
    val activeStages: List<StageProgress> = emptyList(),
    /** Non-null while the conversation is waiting for a human decision. */
    val pending: ApprovalView? = null,
    /** 用户已点击最终确认按钮（或拒绝操作），光效随之关闭，无需等待 Agent 回复完成。 */
    val approvalConfirmed: Boolean = false,
    val otp: String = "",
    val approvalError: String? = null,
    /** Revealed only once the server has actually asked for a login SMS code. */
    val otpRequiredAtLogin: Boolean = false,
    val error: String? = null,
    val outcomeMessageId: String? = null,
    val lastProblem: OperationProblem? = null,
    val showProblem: Boolean = false,
    val notice: String? = null,
    /** Device-management panel visibility and its server-backed rows. */
    val showDevices: Boolean = false,
    val devices: List<DeviceView> = emptyList(),
    val feishuBindingCode: String? = null,
    val feishuCodeExpiresAtMillis: Long = 0,
    val feishuBusy: Boolean = false,
    /** Task-centre panel visibility and its server-backed rows. */
    val showTasks: Boolean = false,
    val tasks: List<TaskView> = emptyList(),
    val splitRequests: List<SplitRequestView> = emptyList(),
    /** 逐题风险测评组件（输入框上方）：非空即展示。 */
    val quiz: RiskQuizUi? = null,
    /** 测评完成后的档案结果（渲染彩色结果卡）。 */
    val profileResult: Map<*, *>? = null,
    /** Predicted next requests shown above the editor; tapping sends immediately. */
    val suggestions: List<String> = DEFAULT_PREDICTED_SUGGESTIONS,
    /** 办理历史页：展示已办理业务与交易流水。 */
    val showHistory: Boolean = false,
    val historyRecords: List<HistoryRecordView> = emptyList(),
    val historyDateScope: String = "ALL",
    val historyCategory: String? = null,
    val selectedHistoryRecord: HistoryRecordView? = null,
    /** 银行卡管理页：卡片列表 + 添加表单。 */
    val showCards: Boolean = false,
    val cards: List<CardView> = emptyList(),
    val cardType: String = "借记卡",
    val cardLabel: String = "",
    val cardFormError: String? = null,
    /** 理财页：银行风格产品超市 → 详情。 */
    val showWealth: Boolean = false,
    val wealthScreen: WealthScreen = WealthScreen.LIST,
    val wealthProducts: List<ProductView> = emptyList(),
    val wealthHoldings: List<HoldingView> = emptyList(),
    val riskProfile: RiskProfileView? = null,
    val selectedProductId: String? = null,
    val buyAmount: String = "",
    val purchaseAmountError: String? = null,
    /** 联系人簿：全屏通讯录式界面（列表 → 详情 → 新增）。 */
    val showContacts: Boolean = false,
    val contactsScreen: ContactsScreen = ContactsScreen.LIST,
    val contacts: List<ContactView> = emptyList(),
    val contactSearch: String = "",
    val selectedContactId: String? = null,
    val contactName: String = "",
    val contactPhone: String = "",
    val contactRelationship: String = "朋友",
    val contactBirthday: String = "",
    val contactFormError: String? = null,
    val contactDeleteId: String? = null,
    val contactDeletingId: String? = null,
    val panelBusy: Boolean = false,
    val taskSubmitting: Boolean = false,
    /**
     * Why the last panel load failed, if it did.
     *
     * Without it an empty list is ambiguous: "the core says you own nothing" and
     * "the core could not be reached" render identically, so a network failure
     * was shown as an authoritative empty state.  The cards / contacts / wealth
     * pages read it to offer a retry instead of "还没有…".
     */
    val panelError: String? = null,
) {
    // A pending approval still permits natural-language correction/cancellation.
    // The gateway withdraws the previous authorization before issuing a revision.
    val canSend: Boolean get() = authenticated && input.isNotBlank() && !busy

    /** 当前打开的理财详情产品。 */
    val selectedProduct: ProductView?
        get() = wealthProducts.firstOrNull { it.productId == selectedProductId }

    /** 金额合法且风险档案等级不低于产品风险时才允许发起申购。 */
    val canBuySelected: Boolean
        get() {
            val product = selectedProduct ?: return false
            // 与 startPurchase 用同一个解析器：按钮可点就等于提交一定成立，
            // 不再让 "1.2.3" 这种文本靠 toDoubleOrNull 悄悄变成 null。
            val amount = parseAmountInput(buyAmount) ?: return false
            if (amount.signum() <= 0 || amount < BigDecimal.valueOf(product.minAmount)) return false
            val profileRank = profileRank(riskProfile?.riskProfile) ?: return false
            return profileRank >= productRank(product.riskLevel)
        }

    /** The add form is only complete when the server-mandated fields make sense. */
    val canAddContact: Boolean
        get() = contactName.isNotBlank() &&
            contactPhone.filter(Char::isDigit).length in 5..20 &&
            (contactBirthday.isBlank() || isValidContactBirthday(contactBirthday))
}

/** Validates that a non-blank birthday is an actual calendar date in [1900-01-01, today]. */
internal fun isValidContactBirthday(birthday: String, today: LocalDate = LocalDate.now()): Boolean {
    if (!birthday.matches(Regex("""\d{4}-\d{2}-\d{2}"""))) return false
    val parsed = runCatching { LocalDate.parse(birthday) }.getOrNull() ?: return false
    return parsed.year >= 1900 && !parsed.isAfter(today)
}

/**
 * 待办审批的光效信号：在用户尚未做出确认/取消决策时持续呼吸；
 * 一旦用户点下最终确认按钮（黄区确认、红区生物识别发起/确认）或取消，光效立刻关闭，无需等待 Agent 后续回复完成。
 */
fun activeApprovalRiskSignal(pending: ApprovalView?, confirmed: Boolean): RiskSignal? {
    if (confirmed || pending == null) return null
    return RiskSignal(
        key = "approval:${pending.approvalId}",
        tier = if (pending.mfaRequired) RiskTier.RED else RiskTier.YELLOW,
        sustained = true,
    )
}
