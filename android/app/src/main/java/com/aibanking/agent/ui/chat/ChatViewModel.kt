package com.aibanking.agent.ui.chat

import android.app.Application
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.aibanking.agent.data.CardView
import com.aibanking.agent.data.CardsRepository
import com.aibanking.agent.data.AgentRepository
import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.data.ContactRepository
import com.aibanking.agent.data.ContactView
import com.aibanking.agent.data.DeviceRepository
import com.aibanking.agent.data.DeviceView
import com.aibanking.agent.data.GatewayException
import com.aibanking.agent.data.LoginRepository
import com.aibanking.agent.data.SessionCheck
import com.aibanking.agent.data.SessionStore
import com.aibanking.agent.data.HoldingView
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.data.RiskProfileView
import com.aibanking.agent.data.TaskRepository
import com.aibanking.agent.data.TaskView
import com.aibanking.agent.data.SplitRequestView
import com.aibanking.agent.data.TurnEnvelope
import com.aibanking.agent.data.ReplyResponse
import com.aibanking.agent.data.StageProgress
import com.aibanking.agent.data.WealthRepository
import com.aibanking.agent.security.DeviceKeyManager
import com.google.gson.Gson
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.math.BigDecimal
import java.security.MessageDigest
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

private val DEFAULT_PREDICTED_SUGGESTIONS = listOf(
    "查一下余额",
    "查看本月账单",
    "理财",
    "查看我的订阅",
)

data class ChatMessage(
    val id: String = UUID.randomUUID().toString(),
    val role: ChatRole,
    val text: String,
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
    val otp: String = "",
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
    /** 银行卡管理页：卡片列表 + 添加表单。 */
    val showCards: Boolean = false,
    val cards: List<CardView> = emptyList(),
    val cardType: String = "借记卡",
    val cardLabel: String = "",
    /** 理财页：银行风格产品超市 → 详情。 */
    val showWealth: Boolean = false,
    val wealthScreen: WealthScreen = WealthScreen.LIST,
    val wealthProducts: List<ProductView> = emptyList(),
    val wealthHoldings: List<HoldingView> = emptyList(),
    val riskProfile: RiskProfileView? = null,
    val selectedProductId: String? = null,
    val buyAmount: String = "",
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
    val panelBusy: Boolean = false,
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
            (contactBirthday.isBlank() || contactBirthday.matches(Regex("""\d{4}-\d{2}-\d{2}""")))
}

/**
 * Drives one conversation against the channel gateway.
 *
 * Nothing security-relevant is decided here: the ViewModel forwards the turn,
 * shows the approval the *server* asked for, and returns the user's decision.
 * The risk tier, the required factors and the final settlement all belong to the
 * core, so a compromised client can annoy the user but cannot authorize a
 * ledger mutation on its own.
 */
class ChatViewModel(application: Application) : AndroidViewModel(application) {

    private val sessionStore = SessionStore(application)
    private var deviceKeys: DeviceKeyManager? = null
    private val agent = AgentRepository.create()
    private val login = LoginRepository.create()
    private val devices = DeviceRepository.create()
    private val tasks = TaskRepository.create()
    private val contacts = ContactRepository.create()
    private val wealth = WealthRepository.create()
    private val cardsRepo = CardsRepository.create()
    private val gson = Gson()

    private val _state = MutableStateFlow(ChatUiState(restoring = true))
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    private val sessionWork = SessionWork(viewModelScope)

    private var conversationId: String = UUID.randomUUID().toString().replace("-", "").take(20)
    private var sessionToken: String? = null
    private var deviceId: String? = null

    /** 正在跑的滑动续期任务，以及核心给出的到期时刻（毫秒）。 */
    private var sessionRenewal: Job? = null
    private var sessionExpiresAtMillis = 0L

    /** 最近一次真实用户手势的时刻：续期只发生在有人正在用这台设备的时候。 */
    private var lastInteractionMillis = System.currentTimeMillis()

    /**
     * 会话过期时被保留下来的未完成问卷属于谁。
     *
     * 问卷内容是用户自己一题一题填的，过期不等于作废；但它只对同一个人有意义，
     * 所以重新登录时要核对身份，另一个用户不能继承上一个人的答题进度。
     */
    private var expiredQuizOwner: String? = null

    /**
     * 进行中的创建提交（开卡 / 加联系人），保留到成功为止。
     *
     * 失败后用户再点一次“确认添加”是同一次提交的重试，必须复用同一个幂等键；
     * 每次调用都新生成一个 UUID 等于把一次超时变成两张开出的卡。
     */
    private var cardCreateAttempt: CreateAttempt? = null
    private var contactCreateAttempt: CreateAttempt? = null

    /** Replaced even for the same user; token renewal keeps this generation. */
    private fun replaceSession() {
        sessionWork.replace {
            sessionToken = null
            deviceId = null
            deviceKeys = null
            sessionStore.clear()
            // Forget this login's enrollment; retain every account's key.
            sessionStore.clearDeviceId()
        }
        stopSessionRenewal()
        reconciliationJobs.clear()
        cardCreateAttempt = null
        contactCreateAttempt = null
        conversationId = UUID.randomUUID().toString().replace("-", "").take(20)
    }

    /** Resolve a renewed credential only while this request still owns the login. */
    private fun SessionWork.Lease.credential(): String? {
        var token: String? = null
        if (!commit { token = sessionToken }) check()
        return token
    }

    init {
        // Restore a previous session so a restart does not force a re-login.
        //
        // 每一次读取都要用 Keystore 解封，放在主线程上会拖住首帧：整段恢复
        // 放到 IO，期间 restoring 为 true，界面显示载入态。
        sessionWork.launch {
            // 解封失败（Keystore 被重置、偏好文件损坏）必须落到登录页：异常逃出
            // 这个协程会让 restoring 永远是 true，界面卡在载入态再也进不去。
            val stored = try {
                io { readStoredSession() }
            } catch (failure: CancellationException) {
                throw failure
            } catch (_: Exception) {
                null
            }
            if (stored == null) {
                _state.update { it.copy(restoring = false) }
                return@launch
            }
            restoreSession(stored)
        }
    }

    /** Everything [SessionStore] holds, read in one IO hop (each value is sealed). */
    private fun readStoredSession() = StoredSession(
        principal = sessionStore.principal(),
        token = sessionStore.sessionToken(),
        conversationId = sessionStore.conversationId(),
        messages = sessionStore.messages(),
        quiz = sessionStore.quiz(),
        expiresAt = sessionStore.sessionExpiresAt(),
    )

    private fun restoreSession(stored: StoredSession) {
        // Revalidate enrollment and select its matching key after restoration.
        // Older installs persisted an id for a single app-wide key; pairing
        // that id with a new account key would make every signature invalid.
        deviceId = null
        deviceKeys = null
        sessionStore.clearDeviceId()
        val storedPrincipal = stored.principal
        val storedToken = stored.token
        if (storedPrincipal.isNullOrBlank() || storedToken.isNullOrBlank()) {
            // 没有可恢复的会话：直接落到登录页。
            _state.update { it.copy(restoring = false) }
            return
        }
        sessionToken = storedToken
        conversationId = stored.conversationId ?: conversationId
        // The transcript is restored too, so a restart keeps the answers the
        // user already received; it is stored sealed, like the credential.
        val restored = stored.messages?.let { json ->
            runCatching { gson.fromJson(json, Array<ChatMessage>::class.java).toList() }.getOrNull()
        }.orEmpty().filterNot { it.isApprovalPreviewMessage() }
        val restoredSuggestions = restored.asReversed()
            .firstOrNull { it.role == ChatRole.ASSISTANT }
            ?.let { predictedSuggestions(TurnEnvelope(result = it.result)) }
            ?: DEFAULT_PREDICTED_SUGGESTIONS
        _state.update {
            it.copy(
                principal = storedPrincipal,
                authenticated = true,
                restoring = false,
                messages = if (restored.isNotEmpty()) restored else listOf(welcome()),
                suggestions = restoredSuggestions,
                // 未答完的问卷组件（含已作答进度）一并恢复，不然重启会退回第一题。
                quiz = parseStoredQuiz(stored.quiz),
            )
        }
        verifyRestoredSession(storedToken, stored.expiresAt)
        initializeDeviceAfterLogin(storedPrincipal)
        restored.filter { it.businessStatus == "PENDING_RECONCILIATION" }.forEach { reconcileMessage(it.id) }
    }

    /**
     * Confirm with the core that a restored credential is still honoured.
     *
     * Sessions live in the core's memory, so a core restart invalidates every one
     * of them while this device still holds a perfectly readable copy.  Showing
     * the chat screen in that state is a lie the user only discovers *after*
     * typing an instruction: the first turn returns 401, the app wipes the
     * session and the login form appears with the request already lost.  Checking
     * once at start-up costs one round-trip and turns that into an honest login
     * screen.  An unreachable core is deliberately *not* treated as expiry.
     */
    private fun verifyRestoredSession(token: String, expiresAtEpochSeconds: Long) {
        sessionWork.launch {
            when (io { login.check(token) }) {
                is SessionCheck.Expired -> {
                    _state.value = expiredSessionState(_state.value)
                }
                // Still valid, or the core could not be reached: keep it either way.
                else -> startSessionRenewal(expiresAtEpochSeconds)
            }
        }
    }

    /**
     * Slide the session forward while the app holds one.
     *
     * The core's window is short on purpose, but an absolute expiry signs out a
     * user who is mid-form: filling in the risk questionnaire costs several
     * minutes and used to be wiped at the 15-minute mark.  Renewing at half the
     * remaining life costs one request per window, and the credential still dies
     * at the core's own expiry once this client stops asking.
     */
    private fun startSessionRenewal(expiresAtEpochSeconds: Long) {
        stopSessionRenewal()
        sessionExpiresAtMillis = expiresAtEpochSeconds * 1000L
        if (sessionExpiresAtMillis <= System.currentTimeMillis()) {
            // No window was recorded (a credential stored before the expiry was
            // persisted): let it lapse as before rather than poll the core.
            return
        }
        sessionRenewal = sessionWork.launch {
            while (isActive) {
                val remaining = sessionExpiresAtMillis - System.currentTimeMillis()
                if (remaining <= 0) return@launch
                delay(maxOf(remaining / 2, MIN_RENEW_DELAY_MILLIS))
                if (sessionToken == null || !_state.value.authenticated) return@launch
                // 空闲续期：手机放在桌上没人碰，会话就该按原来的短 TTL 到期，
                // 不能让一个后台循环把它无限延长——那等于取消了空闲超时。
                if (System.currentTimeMillis() - lastInteractionMillis > IDLE_RENEW_LIMIT_MILLIS) return@launch
                if (renewSession()) {
                    _state.update { if (it.notice == SESSION_EXPIRING_NOTICE) it.copy(notice = null) else it }
                    continue
                }
                val left = sessionExpiresAtMillis - System.currentTimeMillis()
                if (left in 1..SESSION_WARNING_MILLIS) {
                    _state.update { it.copy(notice = SESSION_EXPIRING_NOTICE) }
                }
                delay(RENEW_RETRY_MILLIS)
            }
        }
    }

    /** Swap in a successor credential; false when the core did not hand one over. */
    private suspend fun SessionWork.Lease.renewSession(): Boolean {
        val current = credential() ?: return false
        val renewed = io { login.renew(current) } ?: return false
        // A logout or a second login may have landed while the request was out.
        if (sessionToken != current) return false
        sessionToken = renewed.sessionToken
        sessionExpiresAtMillis = renewed.expiresAt * 1000L
        val conversation = conversationId
        io {
            commit { sessionStore.saveSession(renewed.sessionToken, renewed.userId, conversation, renewed.expiresAt) }
        }
        return true
    }

    private fun stopSessionRenewal() {
        sessionRenewal?.cancel()
        sessionRenewal = null
        sessionExpiresAtMillis = 0L
    }

    private var draftRevision = 0L
    private var voiceGeneration = 0L

    internal fun captureVoiceResult(): (String) -> Unit {
        markUserActivity()
        val voice = ++voiceGeneration
        val lease = sessionWork.capture()
        val revision = draftRevision
        val original = _state.value.input
        val messages = _state.value.messages.size
        return { text ->
            if (lease.isActive && voice == voiceGeneration && _state.value.authenticated && !_state.value.busy && revision == draftRevision &&
                _state.value.input == original && _state.value.messages.size == messages) {
                lease.commit { onInputChange(text.take(2000)) }
            }
        }
    }

    fun onInputChange(value: String) {
        draftRevision++
        markUserActivity()
        _state.update { it.copy(input = value) }
    }

    fun onOtpChange(value: String) {
        markUserActivity()
        _state.update { it.copy(otp = value.filter(Char::isDigit).take(8)) }
    }

    /** 记录一次真实手势，作为“这台设备有人在用”的续期依据。 */
    private fun markUserActivity() {
        lastInteractionMillis = System.currentTimeMillis()
    }

    fun dismissMessages() = _state.update { it.copy(error = null, notice = null) }
    fun openOutcome(messageId: String) = _state.update { state ->
        if (state.authenticated && state.messages.any { it.id == messageId && outcomeFor(it) != null })
            state.copy(outcomeMessageId = messageId, showProblem = false) else state
    }
    fun openProblem() = _state.update { it.copy(showProblem = it.authenticated && it.lastProblem != null, outcomeMessageId = null) }
    fun closeOutcome() = _state.update { it.copy(outcomeMessageId = null, showProblem = false) }
    fun outcomeBalance() {
        // A callback can outlive the enabled state of its button. Unlike a
        // natural-language correction, this shortcut must not replace approval.
        val current = _state.value
        if (!current.authenticated || current.busy || current.pending != null || current.quiz != null) return
        closeOutcome()
        onSuggestionClick("查一下余额")
    }
    fun outcomeTasks() {
        if (!_state.value.authenticated) return
        closeOutcome()
        openTasks()
    }

    /** Surface a local failure (for example a rejected biometric prompt). */
    fun reportError(message: String) = _state.update {
        it.copy(error = message, lastProblem = OperationProblem(message, "DEVICE_VERIFICATION_FAILED", 400))
    }

    /** The prompt belongs to the login and approval visible when it opened. */
    internal fun deviceApprovalCallbacks(approvalId: String): DeviceApprovalCallbacks {
        val owner = sessionWork.capture()
        return DeviceApprovalCallbacks(
            onSuccess = {
                owner.commit {
                    if (_state.value.pending?.approvalId == approvalId && !_state.value.busy) {
                        approveWithDeviceSignature()
                    }
                }
            },
            onFailure = { reason ->
                owner.commit {
                    if (_state.value.pending?.approvalId == approvalId) reportError("生物识别未通过：$reason")
                }
            },
        )
    }

    fun login(userId: String, password: String, otp: String? = null) {
        val principal = userId.trim()
        if (principal.isEmpty()) {
            _state.update { it.copy(error = "请输入用户标识") }
            return
        }
        if (password.isEmpty()) {
            _state.update { it.copy(error = "请输入登录口令") }
            return
        }
        // Supersede restoration, old requests, and any earlier login attempt.
        val previous = _state.value
        val carriedQuiz = previous.quiz
        val needsOtp = previous.otpRequiredAtLogin
        replaceSession()
        _state.value = ChatUiState(busy = true, quiz = carriedQuiz, otpRequiredAtLogin = needsOtp)
        sessionWork.launch {
            _state.update { it.copy(busy = true, error = null, activeStages = emptyList()) }
            try {
                val session = await { login.login(principal, password, otp) }
                sessionToken = session.sessionToken
                markUserActivity()
                sessionStore.saveSession(session.sessionToken, session.userId, conversationId, session.expiresAt)
                // 会话过期时保留下来的问卷只属于同一个人：换个用户登录必须丢弃。
                val carriedQuiz = _state.value.quiz?.takeIf { expiredQuizOwner == session.userId }
                expiredQuizOwner = null
                // A valid banking session is enough to enter the app. Device-key
                // enrollment and FCM are optional post-login work: on some real
                // devices Play Services can take seconds (or never answer), and
                // awaiting it here left the login form spinning even though the
                // session had already been persisted. A process restart then
                // restored that session, which made the symptom look random.
                _state.update {
                    ChatUiState(
                        busy = false,
                        // 上一次失败的原因必须随成功登录一起清掉：留在 state 里会跟着
                        // 进入聊天页，被 ChatHome 的 Snackbar 当成刚发生的事弹出来。
                        error = null,
                        principal = session.userId,
                        authenticated = true,
                        messages = listOf(welcome()),
                        otpRequiredAtLogin = false,
                        quiz = carriedQuiz,
                        notice = if (carriedQuiz != null) {
                            "会话已重新建立，未完成的测评已恢复"
                        } else {
                            "会话有效期 ${session.ttlSeconds} 秒；正在初始化本机安全能力"
                        },
                    )
                }
                startSessionRenewal(session.expiresAt)
                persistMessages()
                persistQuiz(carriedQuiz)
                initializeDeviceAfterLogin(session.userId)
            } catch (failure: GatewayException) {
                check()
                _state.update {
                    it.copy(
                        busy = false,
                        error = failure.message,
                        // Ask for the SMS code only when the deployment actually
                        // requires it, instead of making everyone type one.
                        otpRequiredAtLogin = it.otpRequiredAtLogin || failure.code == "INVALID_LOGIN_OTP",
                    )
                }
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                check()
                _state.update { it.copy(busy = false, error = "登录失败：${failure.message ?: failure::class.java.simpleName}") }
            }
        }
    }

    /** Complete red-tier device setup without blocking the successful login. */
    private fun initializeDeviceAfterLogin(principal: String) {
        sessionWork.launch {
            val enrolled = enrollDevice(principal)
            check()
            if (enrolled == null) {
                _state.update { it.copy(notice = "会话已建立，但本机设备未注册：红色风险操作暂不可用") }
                return@launch
            }
            deviceId = enrolled.first
            deviceKeys = enrolled.second
            sessionStore.saveDeviceId(enrolled.first)
            _state.update { it.copy(notice = "本机设备已注册，高风险操作可用") }
        }
    }

    private suspend fun SessionWork.Lease.enrollDevice(principal: String): Pair<String, DeviceKeyManager>? = try {
        val label = "${Build.MANUFACTURER} ${Build.MODEL}".trim().ifBlank { "Android 设备" }
        val legacyKeys = DeviceKeyManager()
        val legacyId = io { legacyKeys.existingDeviceId() }
        val registered = if (legacyId == null) emptyList() else await { devices.list(principal, credential().orEmpty()) }
        // Keep the old key only for its server-verified owner, including revoked
        // entries. A revoked legacy device must not become a fresh trusted key
        // merely because this installation upgraded or switched accounts.
        val selectedKeys = if (registered.any { it.deviceId == legacyId }) legacyKeys else DeviceKeyManager.forPrincipal(principal)
        // Keystore 读取/首次生成密钥都会阻塞，放到 IO 上；主线程只负责状态。
        val spki = io { selectedKeys.publicKeySpkiBase64() }
        val device = await { devices.register(
            publicKeySpkiBase64 = spki,
            principal = principal,
            sessionToken = credential().orEmpty(),
            label = label,
        ) }
        device.deviceId.takeIf { it.isNotBlank() }?.let { it to selectedKeys }
    } catch (failure: Exception) {
        if (failure is CancellationException) throw failure
        check()
        null
    }

    /**
     * Invalidate local work immediately; revoke the captured credential in the
     * background. A slow logout response must never clear a subsequent login.
     */
    fun logout() {
        val token = sessionToken
        replaceSession()
        expiredQuizOwner = null
        _state.value = ChatUiState()
        viewModelScope.launch {
            if (token != null) {
                try {
                    login.logout(token)
                } catch (failure: CancellationException) {
                    throw failure
                } catch (_: Exception) {
                    // Best effort; the server's short TTL still bounds the token.
                }
            }
        }
    }

    /** Seal the transcript (bounded) so a restart restores the same conversation. */
    private fun persistMessages() {
        val bounded = _state.value.messages.takeLast(MAX_PERSISTED_MESSAGES)
        sessionWork.persist("messages") {
            runCatching { gson.toJson(bounded) }.getOrNull()?.let { sessionStore.saveMessages(it) }
        }
    }

    // -- cloud speech recognition (used only without an on-device recogniser) --

    internal fun cloudSpeechAvailable(onResult: (Boolean) -> Unit) {
        val token = sessionToken ?: return onResult(false)
        sessionWork.launch {
            val available = try {
                io { agent.speechStatus(token) }.cloudAsr
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                false
            }
            onResult(available)
        }
    }

    /** Upload a consented clip; the transcript goes through [deliver] into the input box only. */
    internal fun transcribeCloud(wav: ByteArray, deliver: (String) -> Unit, onFailure: (String) -> Unit) {
        val token = sessionToken ?: return onFailure("请先登录后再使用语音")
        markUserActivity()
        sessionWork.launch {
            try {
                val text = io { agent.transcribe(token, wav) }.text
                if (text.isBlank()) onFailure("没有识别到语音内容，请再说一遍") else deliver(text)
            } catch (failure: GatewayException) {
                check()
                onFailure(failure.message ?: "语音识别失败，请输入文字")
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                check()
                onFailure("语音识别暂时不可用，请输入文字")
            }
        }
    }

    // -- device management panel -------------------------------------------

    fun openDevices() {
        _state.update { it.copy(showDevices = true, showTasks = false, panelBusy = true, feishuBindingCode = null) }
        refreshDevices()
    }

    fun issueFeishuBindingCode() {
        val token = sessionToken ?: return
        _state.update { it.copy(feishuBusy = true, feishuBindingCode = null, error = null) }
        sessionWork.launch {
            try {
                val response = io { agent.issueFeishuBindingCode(token) }
                _state.update { it.copy(feishuBusy = false, feishuBindingCode = response.bindingCode,
                    feishuCodeExpiresAtMillis = System.currentTimeMillis() + response.expiresIn * 1000L) }
                delay(response.expiresIn * 1000L)
                _state.update { current ->
                    if (current.feishuBindingCode == response.bindingCode) current.copy(feishuBindingCode = null) else current
                }
            } catch (failure: GatewayException) {
                check()
                _state.update { it.copy(feishuBusy = false, error = failure.message) }
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                check()
                _state.update { it.copy(feishuBusy = false, error = "无法获取飞书绑定码") }
            }
        }
    }

    fun unbindFeishu() {
        val token = sessionToken ?: return
        _state.update { it.copy(feishuBusy = true, error = null) }
        sessionWork.launch {
            try {
                io { agent.unbindFeishu(token) }
                _state.update { it.copy(feishuBusy = false, feishuBindingCode = null, notice = "飞书绑定已解除") }
            } catch (failure: GatewayException) {
                check()
                _state.update { it.copy(feishuBusy = false, error = failure.message) }
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                check()
                _state.update { it.copy(feishuBusy = false, error = "飞书解绑失败") }
            }
        }
    }

    fun refreshDevices() {
        val principal = _state.value.principal
        sessionWork.launch {
            try {
                val rows = io { devices.list(principal, credential().orEmpty()) }
                _state.update { it.copy(devices = rows, panelBusy = false) }
            } catch (failure: GatewayException) {
                check()
                applyPanelFailure(failure)
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                check()
                // 解析异常（JsonSyntaxException）等非网关失败同样要收尾：逃出协程会让
                // panelBusy 永远为 true，面板停在骨架屏上，连重试按钮都不出现。
                applyPanelFailure(failure)
            }
        }
    }

    fun revokeDevice(targetId: String) {
        val principal = _state.value.principal
        _state.update { it.copy(panelBusy = true, error = null) }
        sessionWork.launch {
            try {
                io { devices.revoke(targetId, principal, credential().orEmpty()) }
                val rows = io { devices.list(principal, credential().orEmpty()) }
                _state.update {
                    it.copy(
                        devices = rows,
                        panelBusy = false,
                        notice = if (targetId == deviceId) "本机已解除注册：红色风险操作在本机不可用" else "设备已停用",
                    )
                }
                if (targetId == deviceId) {
                    // 本机被解除注册：内存与存储都要忘掉，否则重启后 init 会把它读回来，
                    // 应用继续以为本机已注册（红色操作白走一次短信 + 生物识别）。
                    deviceId = null
                    deviceKeys = null
                    sessionStore.clearDeviceId()
                }
            } catch (failure: GatewayException) {
                check()
                applyPanelFailure(failure)
            }
        }
    }

    // -- task centre panel ---------------------------------------------------

    fun openTasks() {
        _state.update { it.copy(showTasks = true, showDevices = false, panelBusy = true) }
        refreshTasks()
    }

    fun refreshTasks() {
        val principal = _state.value.principal
        sessionWork.launch {
            try {
                val rows = io { tasks.list(principal, credential().orEmpty()) }
                val splits = io { tasks.splitInbox(principal, credential().orEmpty()) }
                _state.update { it.copy(tasks = rows, splitRequests = splits, panelBusy = false) }
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

    fun cancelTask(taskId: String) {
        val principal = _state.value.principal
        _state.update { it.copy(panelBusy = true, error = null) }
        sessionWork.launch {
            try {
                io { tasks.cancel(taskId, principal, credential().orEmpty()) }
                val rows = io { tasks.list(principal, credential().orEmpty()) }
                _state.update { it.copy(tasks = rows, panelBusy = false, notice = "任务已取消") }
            } catch (failure: GatewayException) {
                check()
                applyPanelFailure(failure)
            }
        }
    }

    fun takeoverTask(taskId: String, reason: String) {
        if (_state.value.panelBusy || reason.isBlank() || reason.length > 300) return
        if (_state.value.tasks.none { it.taskId == taskId && it.status in setOf("PENDING", "WAITING_USER", "FAILED") }) return
        val principal = _state.value.principal
        _state.update { it.copy(panelBusy = true, error = null) }
        sessionWork.launch {
            try {
                io { tasks.takeover(taskId, reason.trim(), principal, credential().orEmpty()) }
                val rows = io { tasks.list(principal, credential().orEmpty()) }
                _state.update { it.copy(tasks = rows, panelBusy = false, notice = "已暂停关联任务，待人工处理；已完成操作及锁定资金请按回执核对") }
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                check()
                applyPanelFailure(failure)
            }
        }
    }

    fun prepareSplitPayment(request: SplitRequestView) {
        if (request.status != "PENDING" || request.share <= 0 || request.fromName.isBlank()) return
        val amount = BigDecimal.valueOf(request.share).stripTrailingZeros().toPlainString()
        _state.update { it.copy(showTasks = false, input = "给${request.fromName}转${amount}元，备注${request.paymentRemark}", notice = "请核对 AA 金额和收款人后发送，付款仍需您确认") }
    }

    fun declineSplit(splitId: String) {
        val principal = _state.value.principal
        _state.update { it.copy(panelBusy = true, error = null) }
        sessionWork.launch {
            try {
                io { tasks.declineSplit(splitId, principal, credential().orEmpty()) }
                val splits = io { tasks.splitInbox(principal, credential().orEmpty()) }
                _state.update { it.copy(splitRequests = splits, panelBusy = false, notice = "已拒绝 AA 付款请求") }
            } catch (failure: GatewayException) {
                check()
                applyPanelFailure(failure)
            }
        }
    }

    // -- contact book (通讯录式全屏界面) ---------------------------------------

    fun openContacts() {
        _state.update {
            it.copy(
                showContacts = true,
                showCards = false,
                showWealth = false,
                showDevices = false,
                showTasks = false,
                contactsScreen = ContactsScreen.LIST,
                panelBusy = true,
            )
        }
        refreshContacts()
    }

    fun closeContacts() = _state.update { it.copy(showContacts = false) }

    fun showContactDetail(contactId: String) =
        _state.update { it.copy(contactsScreen = ContactsScreen.DETAIL, selectedContactId = contactId) }

    fun openContactEditor() =
        _state.update { it.copy(contactsScreen = ContactsScreen.ADD, error = null) }

    fun backToContactList() =
        _state.update { it.copy(contactsScreen = ContactsScreen.LIST, selectedContactId = null) }

    fun onContactSearchChange(value: String) =
        _state.update { it.copy(contactSearch = value.take(50)) }

    /** Selected contact for the detail page, resolved from the loaded book. */
    fun selectedContact(): ContactView? =
        _state.value.contacts.firstOrNull { it.contactId == _state.value.selectedContactId }

    /**
     * 从联系人详情直接发起转账：把输入框预填成“给 <姓名> 转”，
     * 金额留给用户填写——Agent 收到不完整请求会按规矩澄清，不编造金额。
     */
    fun startTransferTo(name: String) {
        onInputChange("给${name}转")
        closeContacts()
    }

    fun refreshContacts() {
        val principal = _state.value.principal
        sessionWork.launch {
            try {
                val rows = io { contacts.list(principal, credential().orEmpty()) }
                _state.update { it.copy(contacts = rows, panelBusy = false, panelError = null) }
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

    fun onContactFormChange(name: String? = null, phone: String? = null, relationship: String? = null, birthday: String? = null) {
        _state.update { current ->
            current.copy(
                contactName = name ?: current.contactName,
                // The phone field keeps digits only, mirroring the server rule.
                contactPhone = phone?.filter(Char::isDigit)?.take(20) ?: current.contactPhone,
                contactRelationship = relationship ?: current.contactRelationship,
                // Birthday is typed as YYYY-MM-DD digits with dashes preserved.
                contactBirthday = birthday?.filter { it.isDigit() || it == '-' }?.take(10) ?: current.contactBirthday,
            )
        }
    }

    /**
     * Save one contact (name, phone, relationship, birthday).
     *
     * The core validates everything again; whatever it returns, the local form
     * is only cleared when the server actually accepted the row, so a refusal
     * never silently eats what the user typed.
     */
    fun addContact() {
        val principal = _state.value.principal
        val form = _state.value
        if (!form.canAddContact || form.panelBusy) return
        // 同一次提交的所有重试共用一个幂等键（见 [CreateAttempt]）：核心据此回放
        // 响应，读超时后的重发不会建出第二个联系人。
        val attempt = CreateAttempt.of(
            contactCreateAttempt,
            listOf(form.contactName.trim(), form.contactPhone, form.contactRelationship, form.contactBirthday)
                .joinToString("\u0000"),
        )
        contactCreateAttempt = attempt
        _state.update { it.copy(panelBusy = true, error = null) }
        sessionWork.launch {
            try {
                val created = io {
                    contacts.add(
                        principal = principal,
                        sessionToken = credential().orEmpty(),
                        name = form.contactName.trim(),
                        phone = form.contactPhone,
                        relationship = form.contactRelationship,
                        birthday = form.contactBirthday.takeIf { it.isNotBlank() },
                        idempotencyKey = attempt.value,
                    )
                }
                // 已经建好：这次提交到此为止，下一次保存是新的意图、新的键。
                contactCreateAttempt = null
                _state.update {
                    it.copy(
                        panelBusy = false,
                        contactsScreen = ContactsScreen.LIST,
                        contactName = "",
                        contactPhone = "",
                        contactBirthday = "",
                        notice = "联系人 ${created.name} 已添加，可以直接转账",
                    )
                }
                refreshContacts()
            } catch (failure: GatewayException) {
                check()
                applyPanelFailure(failure)
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                check()
                // 与 refresh* 同样的收尾：异常逃出协程会把“保存中…”永远留在界面上，
                // 连重试的机会都没有。
                applyPanelFailure(failure)
            }
        }
    }

    fun removeContact(contactId: String) {
        val principal = _state.value.principal
        _state.update { it.copy(panelBusy = true, error = null) }
        sessionWork.launch {
            try {
                io { contacts.remove(contactId, principal, credential().orEmpty()) }
                _state.update {
                    it.copy(
                        panelBusy = false,
                        contactsScreen = ContactsScreen.LIST,
                        selectedContactId = null,
                        notice = "联系人已删除",
                    )
                }
                refreshContacts()
            } catch (failure: GatewayException) {
                check()
                applyPanelFailure(failure)
            }
        }
    }

    fun dismissPanels() = _state.update { it.copy(showDevices = false, showTasks = false, showContacts = false, showWealth = false, showCards = false, feishuBindingCode = null) }

    // -- bank cards (银行卡管理) ----------------------------------------------

    fun openCards() {
        _state.update { it.copy(showCards = true, showWealth = false, showContacts = false, showDevices = false, showTasks = false, panelBusy = true) }
        refreshCards()
    }

    fun closeCards() = _state.update { it.copy(showCards = false) }

    fun refreshCards() {
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

    fun onCardFormChange(cardType: String? = null, label: String? = null) {
        _state.update { it.copy(cardType = cardType ?: it.cardType, cardLabel = label?.take(40) ?: it.cardLabel) }
    }

    /** 自行绑定银行卡：黄色操作，需用户明确确认并计入审计。 */
    fun addCard(cardNumber: String, source: String) {
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
    suspend fun revealCard(cardId: String, pin: String): com.aibanking.agent.data.CardRevealView {
        val owner = sessionWork.capture()
        val principal = _state.value.principal
        return owner.io { cardsRepo.reveal(principal, owner.credential().orEmpty(), cardId, pin) }
    }

    fun prefillCardTransfer(card: CardView) {
        onInputChange("使用卡片 ${card.cardId}（尾号${card.last4.ifBlank { card.numberMasked.takeLast(4) }}）转账")
        closeCards()
    }

    /** 冻结/挂失/解挂等红色操作：预填话术回对话，走短信+设备签名审批。 */
    fun startCardAction(action: String, card: CardView) {
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

    // -- wealth (银行风格理财页) ----------------------------------------------

    fun openWealth() {
        _state.update { it.copy(showWealth = true, showCards = false, showContacts = false, showDevices = false, showTasks = false, wealthScreen = WealthScreen.LIST, panelBusy = true) }
        refreshWealth()
    }

    fun closeWealth() = _state.update { it.copy(showWealth = false) }

    fun showProductDetail(productId: String) =
        _state.update { it.copy(wealthScreen = WealthScreen.DETAIL, selectedProductId = productId, buyAmount = "") }

    fun backToWealthList() =
        _state.update { it.copy(wealthScreen = WealthScreen.LIST, selectedProductId = null, buyAmount = "") }

    /**
     * 申购金额只接受合法十进制文本：最多一个小数点、小数最多两位。
     *
     * 旧实现只过滤“非数字且非点”的字符，于是 `1.2.3` 也能进输入框，到
     * [startPurchase] 里要么被 `toDoubleOrNull` 判成 null（点了没反应），
     * 要么经 `Double.toString()` 变成科学计数法发给核心。非法编辑直接不采纳
     * （保留上一次的合法文本），而不是先塞进状态再在提交时失败。
     */
    fun onBuyAmountChange(value: String) =
        _state.update {
            if (AMOUNT_INPUT.matches(value)) {
                it.copy(buyAmount = value)
            } else {
                it
            }
        }

    fun refreshWealth() {
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
    fun startPurchase(product: ProductView, amountText: String) {
        val amount = parseAmountInput(amountText) ?: return
        if (amount.signum() <= 0 || amount < BigDecimal.valueOf(product.minAmount)) return
        // toPlainString：`Double.toString()` 会把大额写成 1.0E11 这种科学计数法，
        // 那不是银行话术，也不是核心能解析的金额。
        onInputChange("申购 ${product.productId} ${amount.toPlainString()} 元")
        closeWealth()
    }

    /** Exposed so the device panel can mark "this is this phone" honestly. */
    fun currentDeviceId(): String? = deviceId

    /** 点选一个选项：记录并推进；答完第 8 题自动汇成一条消息提交。 */
    fun onQuizAnswer(question: QuizQuestionUi, key: String) {
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
    fun giveUpQuiz() {
        if (_state.value.quiz == null) return
        _state.update { it.copy(quiz = null) }
        persistQuiz(null)
        sendInternal("退出测评")
    }

    /**
     * 问卷组件状态随手持久化：题目与已作答进度一起存，重装/重启后接着答，
     * 不困在文字兜底里，也不会把答了 5/8 的用户退回第一题。
     */
    private fun persistQuiz(quiz: RiskQuizUi?) {
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
    private fun parseStoredQuiz(json: String?): RiskQuizUi? {
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

    private fun parseQuiz(result: Map<String, Any>?): RiskQuizUi? {
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

    fun send() {
        val current = _state.value
        if (!current.canSend) return
        val text = current.input.trim()
        _state.update { it.copy(input = "") }
        sendInternal(text)
    }

    /** Send a predicted request immediately, matching the one-tap affordance. */
    fun onSuggestionClick(suggestion: String) {
        val prompt = suggestion.trim()
        val current = _state.value
        if (!current.authenticated || current.busy || current.quiz != null || prompt.isEmpty()) return
        _state.update { it.copy(input = "") }
        sendInternal(prompt)
    }

    private val reconciliationJobs = mutableMapOf<String, Job>()
    private var interactionRevision = 0L

    private fun recordStage(progress: StageProgress) {
        _state.update { state ->
            state.copy(activeStages = state.activeStages.filterNot { it.stage == progress.stage && it.step == progress.step } + progress)
        }
    }

    private fun applyEnvelope(
        envelope: TurnEnvelope,
        messageId: String = UUID.randomUUID().toString(),
        updateConversation: Boolean = true,
        clearProblem: Boolean = true,
    ) {
        if (updateConversation) conversationId = envelope.conversationId.ifBlank { conversationId }
        _state.update { state ->
            val previous = state.messages.firstOrNull { it.id == messageId }
            val message = ChatMessage(id = messageId, role = ChatRole.ASSISTANT,
                text = displayText(envelope.response?.summary ?: envelope.reply, envelope.displayResult),
                riskLevel = envelope.riskLevel, approvalPreview = envelope.approval != null,
                result = envelope.displayResult, traceId = envelope.requestId,
                businessStatus = envelope.businessStatus, response = envelope.response,
                conversationId = envelope.conversationId,
                stages = previous?.stages ?: state.activeStages.takeIf { updateConversation })
            // A receipt read started before a newer interaction owns only its
            // history row, never the current approval, editor or navigation.
            if (!updateConversation) return@update state.copy(messages = state.messages.map {
                if (it.id == messageId) message else it
            })
            fun stale(row: ChatMessage): Boolean = row.isApprovalPreviewMessage() ||
                (row.id != messageId && row.businessStatus == "PENDING_RECONCILIATION" &&
                    envelope.response?.operationId != null && row.response?.operationId == envelope.response.operationId)
            val kept = state.messages.filterNot { it.id == messageId || stale(it) }
            val messages = if (previous != null) state.messages.mapNotNull {
                if (it.id == messageId) message else it.takeUnless { row -> stale(row) }
            } else kept + message
            state.copy(busy = false, pending = envelope.approval, otp = "", activeStages = emptyList(),
                lastProblem = if (clearProblem) null else state.lastProblem,
                showProblem = if (clearProblem) false else state.showProblem,
                messages = messages, quiz = parseQuiz(envelope.displayResult),
                profileResult = envelope.displayResult?.get("profile_result") as? Map<*, *>,
                suggestions = predictedSuggestions(envelope))
        }
        persistMessages()
        if (envelope.businessStatus == "PENDING_RECONCILIATION") reconcileMessage(messageId)
    }

    /** Only reads a Core operation receipt. Never resubmits the operation. */
    fun reconcileMessage(messageId: String) {
        if (reconciliationJobs[messageId]?.isActive == true) return
        val message = _state.value.messages.firstOrNull { it.id == messageId } ?: return
        if (!message.hasReconciliationTarget()) return
        val operation = message.response?.operationId ?: return
        val conversation = message.conversationId ?: return
        val principal = _state.value.principal
        if (sessionToken == null) return
        // A newer interaction, including one between polling attempts, ends
        // this reader's ownership of the conversation. Retrying an old history
        // row also cannot acquire the latest turn merely because it is idle.
        val revision = interactionRevision
        reconciliationJobs[messageId] = sessionWork.launch {
            _state.update { it.copy(messages = it.messages.map { row -> if (row.id == messageId) row.copy(reconciling = true, reconciliationError = null) else row }) }
            try {
                repeat(10) { attempt ->
                    if (attempt > 0) delay(2000)
                    check()
                    if (_state.value.messages.none { it.id == messageId && it.hasReconciliationTarget() && it.response?.operationId == operation }) return@launch
                    val result = await { agent.reconcileOperation(principal, credential(), conversation, operation) }
                    val current = _state.value
                    if (current.messages.none { it.id == messageId && it.hasReconciliationTarget() && it.response?.operationId == operation }) return@launch
                    if (result.businessStatus != "PENDING_RECONCILIATION") {
                        val ownsInteraction = revision == interactionRevision && current.messages.lastOrNull()?.id == messageId &&
                            !current.busy && current.pending == null && current.quiz == null
                        applyEnvelope(result, messageId, updateConversation = ownsInteraction, clearProblem = false)
                        return@launch
                    }
                }
            } catch (failure: GatewayException) {
                check()
                if (failure.sessionExpired) applyFailure(failure) else recordReconciliationFailure(messageId, failure.message)
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                check()
                recordReconciliationFailure(messageId, failure.message ?: "暂时无法读取回执")
            } finally {
                commit {
                    _state.update { it.copy(messages = it.messages.map { row -> if (row.id == messageId) row.copy(reconciling = false) else row }) }
                    persistMessages()
                }
            }
        }
    }

    private fun recordReconciliationFailure(messageId: String, reason: String) {
        _state.update { state -> state.copy(messages = state.messages.map { row ->
            if (row.id == messageId && row.hasReconciliationTarget()) row.copy(
                reconciliationError = "核对暂未完成：$reason。原操作结果仍待核对，请勿重复办理。",
            ) else row
        }) }
    }

    private fun sendInternal(text: String, echoUser: Boolean = true) {
        markUserActivity()
        val current = _state.value
        val principal = current.principal
        if (!current.authenticated) return
        interactionRevision++
        // A placeholder assistant bubble carries the live stage label and is then
        // replaced by the real answer, so progress appears where the reply will.
        val placeholder = ChatMessage(role = ChatRole.ASSISTANT, text = "正在处理…")
        _state.update {
            it.copy(
                // 问卷作答在后台提交：答案不进对话，直接等待结果。
                messages = if (echoUser) it.messages + ChatMessage(role = ChatRole.USER, text = text) + placeholder
                else it.messages + placeholder,
                busy = true,
                error = null, lastProblem = null, showProblem = false,
            )
        }
        sessionWork.launch {
            try {
                val envelope = await { agent.streamMessage(
                    principal = principal,
                    sessionToken = credential(),
                    conversationId = conversationId,
                    text = text,
                    messageId = placeholder.id,
                    onProgress = { progress ->
                        commit {
                            _state.update { state -> state.copy(messages = state.messages.map { row ->
                                if (row.id == placeholder.id) row.copy(stages = row.stages.orEmpty().filterNot { it.stage == progress.stage && it.step == progress.step } + progress) else row
                            }) }
                        }
                    },
                ) { label ->
                    commit {
                        _state.update { state ->
                            state.copy(
                                messages = state.messages.map {
                                    if (it.id == placeholder.id) it.copy(text = label) else it
                                },
                            )
                        }
                    }
                } }
                applyEnvelope(envelope, placeholder.id)
            } catch (failure: GatewayException) {
                check()
                // Drop the placeholder: leaving "正在处理…" behind after a
                // failure is exactly the kind of lie this codebase avoids.
                _state.update { state -> state.copy(messages = state.messages.filterNot { it.id == placeholder.id }) }
                persistMessages()
                applyFailure(failure)
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                check()
                // A client-side fault must not take the app down; report it the
                // same way a server-side refusal is reported.
                _state.update { state ->
                    state.copy(
                        messages = state.messages.filterNot { it.id == placeholder.id },
                    )
                }
                persistMessages()
                applyFailure(GatewayException("CLIENT_ERROR", "未能读取本次请求的结果，请先核对现有回执。", 0))
            }
        }
    }

    fun reject() {
        val approval = _state.value.pending ?: return
        resolve(approval, decision = "reject")
    }

    /** Yellow tier: the click on the approval card is the whole factor. */
    fun approve() {
        val approval = _state.value.pending ?: return
        if (approval.mfaRequired) {
            _state.update { it.copy(error = "该操作需要短信验证码与设备签名，请完成强验证") }
            return
        }
        resolve(approval, decision = "approve")
    }

    /**
     * Red tier: fetch the server's liveness challenge, sign it with the device
     * key, and hand the signature plus the SMS code back for verification.
     *
     * Called only after the local biometric prompt succeeded.
     */
    fun approveWithDeviceSignature() {
        if (_state.value.busy) return
        val approval = _state.value.pending ?: return
        val otp = _state.value.otp
        if (otp.length < 6) {
            _state.update { it.copy(error = "请输入 6 位短信验证码") }
            return
        }
        // Without an enrolled key the signature would be unverifiable, so say so
        // instead of sending an attestation the core is guaranteed to refuse.
        val enrolledDevice = deviceId
        val signingKeys = deviceKeys
        if (enrolledDevice.isNullOrBlank() || signingKeys == null) {
            _state.update { it.copy(error = "本机尚未完成设备注册，无法完成红色风险验证") }
            return
        }
        val principal = _state.value.principal
        interactionRevision++
        _state.update { it.copy(busy = true, error = null, lastProblem = null, showProblem = false, activeStages = emptyList()) }
        sessionWork.launch {
            var approvalSubmitted = false
            try {
                val challenge = await { agent.requestFaceChallenge(principal, credential(), approval.approvalId) }
                // The private key stays in the Keystore; this returns only a
                // signature over the canonical challenge string.  Signing is
                // Keystore work too, so it happens off the main thread.
                val signature = io {
                    signingKeys.signChallenge(
                        challengeId = challenge.challengeId,
                        userId = principal,
                        authTokenJti = challenge.authTokenJti,
                    )
                }
                val envelope = DeviceKeyManager.attestationEnvelope(
                    deviceId = enrolledDevice,
                    challengeId = challenge.challengeId,
                    signatureBase64 = signature,
                )
                approvalSubmitted = true
                val resolved = await { agent.resolveApproval(
                    principal = principal,
                    sessionToken = credential(),
                    approvalId = approval.approvalId,
                    decision = "approve",
                    otp = otp,
                    faceAttestation = envelope,
                    onProgress = { progress -> commit { recordStage(progress) } },
                ) }
                applyEnvelope(resolved)
            } catch (failure: GatewayException) {
                check()
                applyFailure(failure)
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                check()
                // Only local validation before submission can be called a
                // device failure; an unreadable approval reply is uncertain.
                applyFailure(if (approvalSubmitted) {
                    GatewayException("CLIENT_ERROR", "未能读取本次办理结果，请先核对现有回执。", 0)
                } else {
                    GatewayException("DEVICE_VERIFICATION_FAILED", "本机验证未完成，请返回原审批卡检查设备验证设置。", 400)
                })
            }
        }
    }

    fun requestDemoOtp() {
        val approval = _state.value.pending ?: return
        if (!approval.mfaRequired || _state.value.busy) return
        val principal = _state.value.principal
        _state.update { it.copy(busy = true, error = null) }
        sessionWork.launch {
            try {
                val result = await { agent.requestDemoOtp(principal, credential(), approval.approvalId) }
                if (_state.value.pending?.approvalId == approval.approvalId && result.approvalId == approval.approvalId) {
                    val code = result.demoCode?.takeIf { it.matches(Regex("[0-9]{6}")) }
                        ?: throw GatewayException("CORE_PROTOCOL_ERROR", "未返回有效演示验证码", 502)
                    _state.update { it.copy(busy = false, notice = "本机演示验证码：$code，有效 ${result.expiresIn} 秒；未发送短信") }
                } else _state.update { it.copy(busy = false) }
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                check()
                applyFailure(if (failure is GatewayException) failure else GatewayException("CLIENT_ERROR", "无法获取演示验证码，请重试", 0))
                _state.update { it.copy(busy = false) }
            }
        }
    }

    private fun resolve(approval: ApprovalView, decision: String) {
        if (_state.value.busy) return
        markUserActivity()
        val principal = _state.value.principal
        interactionRevision++
        _state.update { it.copy(busy = true, error = null, lastProblem = null, showProblem = false, activeStages = emptyList()) }
        sessionWork.launch {
            try {
                val envelope = await { agent.resolveApproval(
                    principal = principal,
                    sessionToken = credential(),
                    approvalId = approval.approvalId,
                    decision = decision,
                    onProgress = { progress -> commit { recordStage(progress) } },
                ) }
                applyEnvelope(envelope)
            } catch (failure: GatewayException) {
                check()
                applyFailure(failure)
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                check()
                applyFailure(GatewayException("CLIENT_ERROR", "未能读取本次办理结果，请先核对现有回执。", 0))
            }
        }
    }

    /**
     * Drop the credential but keep the user's unfinished form.
     *
     * A TTL expiry is not the user abandoning the questionnaire: they were on
     * question 8 and used to lose all of it.  The credential and the transcript
     * go (they belong to the authenticated session); the in-progress quiz stays
     * in state, tagged with its owner, so the same person gets it back after
     * re-login and nobody else can.
     */
    private fun expiredSessionState(state: ChatUiState): ChatUiState {
        replaceSession()
        expiredQuizOwner = state.principal.takeIf { state.quiz != null }
        return ChatUiState(error = "登录状态已失效，请重新登录", quiz = state.quiz)
    }

    private fun applyFailure(failure: GatewayException) {
        if (failure.sessionExpired) {
            _state.value = expiredSessionState(_state.value)
            return
        }
        _state.update { state ->
            // 审批/验证失败时短信验证码已经被核心消费或拒绝，字段必须清空：
            // 留着它，用户再次点“确认执行”只会把同一个失效的码再送一次。
            state.copy(busy = false, error = failure.message, otp = "", activeStages = emptyList(),
                lastProblem = OperationProblem(failure.message, failure.code, failure.httpStatus))
        }
    }

    /**
     * A panel failure is a chat failure that clears a different flag.
     *
     * The card / wealth / contact / device / task panels used to report the
     * message and stop there, so a routine session expiry - sessions are
     * short-lived and live in the core's memory - left the user "logged in" on a
     * page where every action then failed.  Sharing the chat path's session check
     * means an expiry always lands back on the login screen.
     *
     * `panelError` is kept as well as `error` because the panels need it after
     * the snackbar is dismissed: an empty list plus a failure reason is "we could
     * not ask", which must not render as "you own nothing".
     */
    private fun applyPanelFailure(failure: GatewayException) {
        if (failure.sessionExpired) {
            _state.value = expiredSessionState(_state.value)
            return
        }
        _state.update { state ->
            state.copy(panelBusy = false, panelError = failure.message, error = failure.message)
        }
    }

    /**
     * 非网关失败（响应解析失败、客户端自身异常）的面板收尾。
     *
     * 与上面的重载走同一条路：清掉 panelBusy、留下 panelError，页面据此显示
     * 真正的失败与重试按钮，而不是把“没问到核心”渲染成“你没有卡片/联系人”。
     * 这类异常没有 HTTP 状态，所以不做会话失效判断。
     */
    private fun applyPanelFailure(failure: Exception) {
        val message = "面板加载失败：${failure.message ?: failure::class.java.simpleName}"
        _state.update { it.copy(panelBusy = false, panelError = message, error = message) }
    }

    private fun welcome() = ChatMessage(
        role = ChatRole.ASSISTANT,
        text = "您好！我可以帮您查询余额/账单、转账、分析消费、管理理财、卡片和订阅。高风险操作需要您完成短信验证与设备签名。",
    )

    private companion object {
        /** Transcript bound kept sealed in preferences - a chat log is not a ledger. */
        const val MAX_PERSISTED_MESSAGES = 100

        /** 续期节奏：半程续期、失败后 30 秒重试、到期前 2 分钟开始提醒。 */
        const val MIN_RENEW_DELAY_MILLIS = 5_000L
        const val RENEW_RETRY_MILLIS = 30_000L
        const val SESSION_WARNING_MILLIS = 120_000L
        const val SESSION_EXPIRING_NOTICE = "会话即将过期，正在重试续期；请先确认或保存未完成的操作"

        /** 超过这个时长没有任何手势就视为空闲，不再续期。 */
        const val IDLE_RENEW_LIMIT_MILLIS = 15 * 60 * 1000L
    }
}

/** Also recognizes preview cards persisted before [ChatMessage.approvalPreview] existed. */
private fun ChatMessage.isApprovalPreviewMessage(): Boolean =
    approvalPreview || (role == ChatRole.ASSISTANT && text.trimStart().startsWith("您正准备进行："))

/**
 * Follow-ups after a reservation completes. The first phrase is the rollback
 * request; the gateway parses it as UNDO_OPERATION of the same kind (pinned by
 * tests/test_undo_and_card_channel.py), and Core still asks for confirmation.
 */
internal val UNDO_SUGGESTIONS: Map<String, List<String>> = mapOf(
    "SCHEDULED_TRANSFER" to listOf("撤销这笔预约", "查看账户余额", "再转一笔"),
    "SPLIT_BILL" to listOf("撤回这笔 AA 请求", "查看账户余额", "查询最近转账"),
    "LOCK_SUCCESS" to listOf("取消生日锁定", "查看账户余额", "查看本月账单"),
)

/** Derive bounded, actionable next turns from the completed server turn. */
internal fun predictedSuggestions(envelope: TurnEnvelope): List<String> {
    // Approval controls always win. Graph state can retain suggestions from an
    // earlier turn, and those must never displace the safe revision choices.
    if (envelope.approval != null) {
        return listOf("取消这次操作", "修改这次操作", "更换办理对象")
    }

    val serverSuggestions = (envelope.result?.get("suggestions") as? List<*>)
        .orEmpty()
        .mapNotNull { value ->
            value?.toString()?.trim()?.takeIf { it.isNotEmpty() && it.length <= 80 }
        }
        .distinct()
        .take(4)

    val execution = envelope.result?.get("execution_result") as? Map<*, *>
    val executionType = execution?.get("type")?.toString().orEmpty()
    val context = (envelope.intent.orEmpty() + " " + executionType).uppercase()
    // Server-authored choices belong to the just-completed risk assessment.
    // In other flows they may be stale LangGraph state, so the current intent
    // and verified execution result must determine the next actions instead.
    val currentRiskAssessment =
        envelope.result?.get("profile_result") != null || "WEALTH_RISK_ASSESSMENT" in context
    if (currentRiskAssessment && serverSuggestions.isNotEmpty()) return serverSuggestions

    // A reservation that has not moved money can be withdrawn; offer that first,
    // keyed on the verified execution type so a failed attempt never shows it.
    UNDO_SUGGESTIONS[executionType]?.let { return it }

    return when {
        "TRANSFER" in context || "SPLIT_BILL" in context ->
            listOf("查看账户余额", "查询最近转账", "再转一笔")
        "BALANCE" in context ->
            listOf("查看本月账单", "分析本月消费", "给联系人转账")
        "BILL" in context || "TRANSACTION" in context ->
            listOf("分析本月消费", "查找异常交易", "查看账户余额")
        "WEALTH" in context ->
            listOf("查看理财产品", "比较理财产品", "查看我的持仓")
        "CARD" in context ->
            listOf("查看我的卡片", "调整卡片额度", "挂失银行卡")
        "SUBSCRIPTION" in context -> {
            val cancelSuggestion = activeSubscriptionCancelSuggestion(execution)
            if (cancelSuggestion != null) {
                listOf(cancelSuggestion, "查看下次扣费", "分析订阅支出")
            } else {
                listOf("查看我的订阅", "查看下次扣费", "分析订阅支出")
            }
        }
        "CROSS_SCENE" in context || "LOCK_FUND" in context ->
            listOf("查看账户余额", "查看本月账单", "查看我的订阅")
        else -> serverSuggestions.ifEmpty { DEFAULT_PREDICTED_SUGGESTIONS }
    }
}

/** Build a cancellation action only from an active subscription returned by the core. */
private fun activeSubscriptionCancelSuggestion(execution: Map<*, *>?): String? {
    if (execution?.get("type")?.toString() != "SUBSCRIPTION_QUERY") return null
    val response = execution["data"] as? Map<*, *> ?: return null
    val subscriptions = response["data"] as? List<*> ?: return null
    val merchant = subscriptions.asSequence()
        .mapNotNull { it as? Map<*, *> }
        .firstOrNull { it["status"]?.toString() == "ACTIVE" }
        ?.get("merchant")
        ?.toString()
        ?.trim()
        ?.takeIf { it.isNotEmpty() && it.length <= 60 }
        ?: return null
    val subscriptionName = if (merchant.endsWith("订阅")) merchant else "$merchant 订阅"
    return "取消 $subscriptionName"
}

/**
 * Everything [SessionStore] hands back at start-up.
 *
 * Read as one batch so the restore costs a single IO hop: every read unseals a
 * Keystore-encrypted value, which must not happen on the main thread.
 */
private class StoredSession(
    val principal: String?,
    val token: String?,
    val conversationId: String?,
    val messages: String?,
    val quiz: String?,
    val expiresAt: Long = 0,
)

/**
 * 一次“用户发起的创建”的幂等身份。
 *
 * [fingerprint] 是这次提交的内容，[value] 是随请求发出的 `Idempotency-Key`。
 * 核心按 (用户, 端点, 键) 记录：同一份内容的重试必须复用同一个键，否则一次读
 * 超时后的重发就是第二次创建（多出一张卡或一个联系人）；而内容一变就必须换新
 * 键，否则核心会对同一个键的另一个载荷回 `IDEMPOTENCY_KEY_REUSE`(409)。
 */
private class CreateAttempt(val fingerprint: String, val value: String) {
    companion object {
        fun of(previous: CreateAttempt?, fingerprint: String): CreateAttempt =
            if (previous != null && previous.fingerprint == fingerprint) {
                previous
            } else {
                CreateAttempt(fingerprint, UUID.randomUUID().toString())
            }
    }
}

/**
 * 风险档案 → 可购买的最高产品等级，与服务端 `profile_rank` 保持一致。
 *
 * 服务端有两套词汇：老表单产出 CONSERVATIVE/BALANCED/AGGRESSIVE，8 题问卷产出
 * C1–C5。只认前者会把刚做完问卷的用户判成"未测评"——详情页一边显示"激进型"、
 * 一边说"不适合 R1"，购买入口永远不可用。
 */
internal fun profileRank(profile: String?): Int? = when (profile) {
    "CONSERVATIVE", "C1" -> 1
    "BALANCED", "C2" -> 2
    "C3" -> 3
    "AGGRESSIVE", "C4" -> 4
    "C5" -> 5
    else -> null
}

internal fun productRank(riskLevel: String): Int = riskLevel.removePrefix("R").toIntOrNull() ?: 9

/**
 * 申购金额的输入文法：最多一个小数点、小数最多两位、整数部分最多十位。
 *
 * 空串与孤立的 “.” 是输入过程中的中间态（用户先打小数点），匹配得上但不是
 * 金额，由 [parseAmountInput] 判掉。
 */
private val AMOUNT_INPUT = Regex("""\d{0,10}(\.\d{0,2})?""")

/**
 * 金额文本 → 金额；非法文本返回 null。
 *
 * 全程走 [BigDecimal]，不经过 Double：十进制文本→Double→字符串的往返会改写
 * 用户输入（0.1+0.2 类误差），大额还会变成科学计数法。
 */
internal fun parseAmountInput(text: String): BigDecimal? {
    val trimmed = text.trim()
    if (!AMOUNT_INPUT.matches(trimmed)) return null
    // "." 或 "" 能过文法但不是金额，BigDecimal 会直接抛，交给 runCatching。
    return runCatching { BigDecimal(trimmed) }.getOrNull()
}

/**
 * 理财回复的产品清单以卡片渲染（见 ResultView），气泡文本里保留头行、
 * 风险提示等说明，去掉会重复的“- `代码` …”清单行。
 */
private fun displayText(reply: String, result: Map<String, Any>?): String {
    var lines = reply.lines()
    val type = (result?.get("execution_result") as? Map<*, *>)?.get("type")?.toString()
    if (type in setOf("WEALTH_QUERY", "WEALTH_RECOMMEND", "WEALTH_COMPARE")) {
        lines = lines.filterNot { it.trimStart().startsWith("- `") }
    }
    // 测评结果的三行说明渲染成彩色结果卡，文本里不再重复。
    if (result?.get("profile_result") != null) {
        lines = lines.filterNot {
            it.startsWith("✅ 风险测评完成") || it.startsWith("特点：") || it.startsWith("匹配理财产品级别：")
        }
    }
    return lines.filter { it.isNotBlank() }.joinToString("\n")
}
