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
import com.aibanking.agent.data.WealthRepository
import com.aibanking.agent.push.PushNotifications
import com.aibanking.agent.security.DeviceKeyManager
import com.google.gson.Gson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.math.BigDecimal
import java.security.MessageDigest
import java.util.UUID

enum class ChatRole { USER, ASSISTANT }

/** 通讯录式联系人界面的三个页面：列表 → 详情 → 新增。 */
enum class ContactsScreen { LIST, DETAIL, ADD }

/** 理财页两个页面：产品超市 → 产品详情（申购预填回聊天）。 */
enum class WealthScreen { LIST, DETAIL }

data class ChatMessage(
    val id: String = UUID.randomUUID().toString(),
    val role: ChatRole,
    val text: String,
    val riskLevel: String? = null,
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
    /** Non-null while the conversation is waiting for a human decision. */
    val pending: ApprovalView? = null,
    val otp: String = "",
    /** Revealed only once the server has actually asked for a login SMS code. */
    val otpRequiredAtLogin: Boolean = false,
    val error: String? = null,
    val notice: String? = null,
    /** Device-management panel visibility and its server-backed rows. */
    val showDevices: Boolean = false,
    val devices: List<DeviceView> = emptyList(),
    /** Task-centre panel visibility and its server-backed rows. */
    val showTasks: Boolean = false,
    val tasks: List<TaskView> = emptyList(),
    /** 逐题风险测评组件（输入框上方）：非空即展示。 */
    val quiz: RiskQuizUi? = null,
    /** 测评完成后的档案结果（渲染彩色结果卡）。 */
    val profileResult: Map<String, Any>? = null,
    /** 回复末尾的快捷输入建议（点击填入输入框）。 */
    val suggestions: List<String> = emptyList(),
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
    val canSend: Boolean get() = authenticated && input.isNotBlank() && !busy && pending == null

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
    private val deviceKeys = DeviceKeyManager()
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

    private var conversationId: String = UUID.randomUUID().toString().replace("-", "").take(20)
    private var sessionToken: String? = null
    private var deviceId: String? = null

    /**
     * 进行中的创建提交（开卡 / 加联系人），保留到成功为止。
     *
     * 失败后用户再点一次“确认添加”是同一次提交的重试，必须复用同一个幂等键；
     * 每次调用都新生成一个 UUID 等于把一次超时变成两张开出的卡。
     */
    private var cardCreateAttempt: CreateAttempt? = null
    private var contactCreateAttempt: CreateAttempt? = null

    init {
        // Restore a previous session so a restart does not force a re-login.
        //
        // 每一次读取都要用 Keystore 解封，放在主线程上会拖住首帧：整段恢复
        // 放到 IO，期间 restoring 为 true，界面显示载入态。
        viewModelScope.launch {
            // 解封失败（Keystore 被重置、偏好文件损坏）必须落到登录页：异常逃出
            // 这个协程会让 restoring 永远是 true，界面卡在载入态再也进不去。
            val stored = runCatching { withContext(Dispatchers.IO) { readStoredSession() } }.getOrNull()
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
        deviceId = sessionStore.deviceId(),
        conversationId = sessionStore.conversationId(),
        messages = sessionStore.messages(),
        quiz = sessionStore.quiz(),
    )

    private fun restoreSession(stored: StoredSession) {
        deviceId = stored.deviceId
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
        }.orEmpty()
        _state.update {
            it.copy(
                principal = storedPrincipal,
                authenticated = true,
                restoring = false,
                messages = if (restored.isNotEmpty()) restored else listOf(welcome()),
                // 未答完的问卷组件（含已作答进度）一并恢复，不然重启会退回第一题。
                quiz = parseStoredQuiz(stored.quiz),
            )
        }
        verifyRestoredSession(storedToken)
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
    private fun verifyRestoredSession(token: String) {
        viewModelScope.launch {
            when (withContext(Dispatchers.IO) { login.check(token) }) {
                is SessionCheck.Expired -> {
                    sessionToken = null
                    sessionStore.clear()
                    _state.update {
                        it.copy(
                            authenticated = false,
                            principal = "",
                            busy = false,
                            messages = listOf(welcome()),
                            error = "登录状态已失效，请重新登录",
                        )
                    }
                }
                // Still valid, or the core could not be reached: keep it either way.
                else -> Unit
            }
        }
    }

    fun onInputChange(value: String) = _state.update { it.copy(input = value) }

    fun onOtpChange(value: String) = _state.update { it.copy(otp = value.filter(Char::isDigit).take(8)) }

    fun dismissMessages() = _state.update { it.copy(error = null, notice = null) }

    /** Surface a local failure (for example a rejected biometric prompt). */
    fun reportError(message: String) = _state.update { it.copy(error = message) }

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
        viewModelScope.launch {
            _state.update { it.copy(busy = true, error = null) }
            try {
                val session = login.login(principal, password, otp)
                sessionToken = session.sessionToken
                sessionStore.saveSession(session.sessionToken, session.userId, conversationId)
                // Registering the device key is what makes the red tier usable.
                // A failure here is reported but does not block login: green and
                // yellow operations need no device key at all.
                val enrolled = enrollDevice(session.sessionToken, session.userId)
                // Notifications can only be addressed to an enrolled device, and
                // only once a Firebase project is configured - an unconfigured
                // build simply registers nothing here.
                if (enrolled) registerPushToken(session.sessionToken, session.userId)
                _state.update {
                    it.copy(
                        busy = false,
                        // 上一次失败的原因必须随成功登录一起清掉：留在 state 里会跟着
                        // 进入聊天页，被 ChatHome 的 Snackbar 当成刚发生的事弹出来。
                        error = null,
                        principal = session.userId,
                        authenticated = true,
                        messages = listOf(welcome()),
                        otpRequiredAtLogin = false,
                        notice = if (enrolled) {
                            "会话有效期 ${session.ttlSeconds} 秒；本机设备已注册"
                        } else {
                            "会话已建立，但本机设备未注册：红色风险操作暂不可用"
                        },
                    )
                }
                persistMessages()
            } catch (failure: GatewayException) {
                _state.update {
                    it.copy(
                        busy = false,
                        error = failure.message,
                        // Ask for the SMS code only when the deployment actually
                        // requires it, instead of making everyone type one.
                        otpRequiredAtLogin = it.otpRequiredAtLogin || failure.code == "INVALID_LOGIN_OTP",
                    )
                }
            }
        }
    }

    private suspend fun enrollDevice(token: String, principal: String): Boolean = try {
        val label = "${Build.MANUFACTURER} ${Build.MODEL}".trim().ifBlank { "Android 设备" }
        // Keystore 读取/首次生成密钥都会阻塞，放到 IO 上；主线程只负责状态。
        val spki = withContext(Dispatchers.IO) { deviceKeys.publicKeySpkiBase64() }
        val device = devices.register(
            publicKeySpkiBase64 = spki,
            principal = principal,
            sessionToken = token,
            label = label,
        )
        deviceId = device.deviceId
        sessionStore.saveDeviceId(device.deviceId)
        true
    } catch (failure: Exception) {
        false
    }

    /**
     * Hand the server this device's notification token, if there is one.
     *
     * Reads the token off the main thread (the FCM lookup blocks) and stays
     * silent when push is unconfigured: a build without a Firebase project has
     * no token to register, and inventing one would make the server's delivery
     * log claim something that cannot happen.
     */
    private suspend fun registerPushToken(sessionToken: String, principal: String) = withContext(Dispatchers.IO) {
        val device = deviceId ?: return@withContext
        val token = PushNotifications.currentToken(getApplication()) ?: return@withContext
        runCatching { devices.registerPushToken(device, token, principal, sessionToken) }
        Unit
    }

    /**
     * Sign out: revoke the session on the server first, then forget it locally.
     *
     * The order matters - wiping local storage before the server has revoked
     * the credential would leave a copied token valid until expiry.  The
     * revocation is best effort: when the network is gone (or the session
     * already expired) the local wipe still happens, and the token dies at its
     * short TTL regardless.
     */
    fun logout() {
        val token = sessionToken
        viewModelScope.launch {
            if (token != null) {
                withContext(Dispatchers.IO) { runCatching { login.logout(token) } }
            }
            sessionToken = null
            sessionStore.clear()
            conversationId = UUID.randomUUID().toString().replace("-", "").take(20)
            _state.value = ChatUiState()
        }
    }

    /** Seal the transcript (bounded) so a restart restores the same conversation. */
    private fun persistMessages() {
        val bounded = _state.value.messages.takeLast(MAX_PERSISTED_MESSAGES)
        runCatching { gson.toJson(bounded) }.getOrNull()?.let { sessionStore.saveMessages(it) }
    }

    // -- device management panel -------------------------------------------

    fun openDevices() {
        _state.update { it.copy(showDevices = true, showTasks = false, panelBusy = true) }
        refreshDevices()
    }

    fun refreshDevices() {
        val principal = _state.value.principal
        viewModelScope.launch {
            try {
                val rows = withContext(Dispatchers.IO) { devices.list(principal, sessionToken ?: "") }
                _state.update { it.copy(devices = rows, panelBusy = false) }
            } catch (failure: GatewayException) {
                applyPanelFailure(failure)
            } catch (failure: Exception) {
                // 解析异常（JsonSyntaxException）等非网关失败同样要收尾：逃出协程会让
                // panelBusy 永远为 true，面板停在骨架屏上，连重试按钮都不出现。
                applyPanelFailure(failure)
            }
        }
    }

    fun revokeDevice(targetId: String) {
        val principal = _state.value.principal
        _state.update { it.copy(panelBusy = true, error = null) }
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { devices.revoke(targetId, principal, sessionToken ?: "") }
                val rows = withContext(Dispatchers.IO) { devices.list(principal, sessionToken ?: "") }
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
                    sessionStore.clearDeviceId()
                }
            } catch (failure: GatewayException) {
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
        viewModelScope.launch {
            try {
                val rows = withContext(Dispatchers.IO) { tasks.list(principal, sessionToken ?: "") }
                _state.update { it.copy(tasks = rows, panelBusy = false) }
            } catch (failure: GatewayException) {
                applyPanelFailure(failure)
            } catch (failure: Exception) {
                applyPanelFailure(failure)
            }
        }
    }

    fun cancelTask(taskId: String) {
        val principal = _state.value.principal
        _state.update { it.copy(panelBusy = true, error = null) }
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { tasks.cancel(taskId, principal, sessionToken ?: "") }
                val rows = withContext(Dispatchers.IO) { tasks.list(principal, sessionToken ?: "") }
                _state.update { it.copy(tasks = rows, panelBusy = false, notice = "任务已取消") }
            } catch (failure: GatewayException) {
                applyPanelFailure(failure)
            }
        }
    }

    // -- contact book (通讯录式全屏界面) ---------------------------------------

    fun openContacts() {
        _state.update {
            it.copy(
                showContacts = true,
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
        viewModelScope.launch {
            try {
                val rows = withContext(Dispatchers.IO) { contacts.list(principal, sessionToken ?: "") }
                _state.update { it.copy(contacts = rows, panelBusy = false, panelError = null) }
            } catch (failure: GatewayException) {
                applyPanelFailure(failure)
            } catch (failure: Exception) {
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
        viewModelScope.launch {
            try {
                val created = withContext(Dispatchers.IO) {
                    contacts.add(
                        principal = principal,
                        sessionToken = sessionToken ?: "",
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
                applyPanelFailure(failure)
            } catch (failure: Exception) {
                // 与 refresh* 同样的收尾：异常逃出协程会把“保存中…”永远留在界面上，
                // 连重试的机会都没有。
                applyPanelFailure(failure)
            }
        }
    }

    fun removeContact(contactId: String) {
        val principal = _state.value.principal
        _state.update { it.copy(panelBusy = true, error = null) }
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { contacts.remove(contactId, principal, sessionToken ?: "") }
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
                applyPanelFailure(failure)
            }
        }
    }

    fun dismissPanels() = _state.update { it.copy(showDevices = false, showTasks = false, showContacts = false, showWealth = false, showCards = false) }

    // -- bank cards (银行卡管理) ----------------------------------------------

    fun openCards() {
        _state.update { it.copy(showCards = true, showWealth = false, showContacts = false, showDevices = false, showTasks = false, panelBusy = true) }
        refreshCards()
    }

    fun closeCards() = _state.update { it.copy(showCards = false) }

    fun refreshCards() {
        val principal = _state.value.principal
        viewModelScope.launch {
            try {
                val rows = withContext(Dispatchers.IO) { cardsRepo.list(principal, sessionToken ?: "") }
                _state.update { it.copy(cards = rows, panelBusy = false, panelError = null) }
            } catch (failure: GatewayException) {
                applyPanelFailure(failure)
            } catch (failure: Exception) {
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
        viewModelScope.launch {
            try {
                val created = withContext(Dispatchers.IO) {
                    cardsRepo.add(principal, sessionToken ?: "", form.cardType, form.cardLabel, attempt.value, cardNumber, source)
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
                applyPanelFailure(failure)
            } catch (failure: Exception) {
                // 失败必须收尾：panelBusy 留在 true 会让添加表单永远停在“开卡中…”。
                applyPanelFailure(failure)
            }
        }
    }

    /** PIN stays in the detail composable; this method never stores the reveal result. */
    suspend fun revealCard(cardId: String, pin: String): com.aibanking.agent.data.CardRevealView =
        withContext(Dispatchers.IO) { cardsRepo.reveal(_state.value.principal, sessionToken ?: "", cardId, pin) }

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
        viewModelScope.launch {
            try {
                val products = withContext(Dispatchers.IO) { wealth.products(principal, sessionToken ?: "") }
                val holdings = withContext(Dispatchers.IO) { wealth.holdings(principal, sessionToken ?: "") }
                val profile = withContext(Dispatchers.IO) { wealth.riskProfile(principal, sessionToken ?: "") }
                _state.update { it.copy(wealthProducts = products, wealthHoldings = holdings, riskProfile = profile, panelBusy = false, panelError = null) }
            } catch (failure: GatewayException) {
                applyPanelFailure(failure)
            } catch (failure: Exception) {
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
        if (quiz == null) {
            sessionStore.clearQuiz()
            return
        }
        runCatching { gson.toJson(quiz) }.getOrNull()?.let { sessionStore.saveQuiz(it) }
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

    /** 点击快捷建议：填入输入框，用户可修改后自行发送。 */
    fun onSuggestionClick(suggestion: String) = onInputChange(suggestion)

    private fun sendInternal(text: String, echoUser: Boolean = true) {
        val current = _state.value
        val principal = current.principal
        // A placeholder assistant bubble carries the live stage label and is then
        // replaced by the real answer, so progress appears where the reply will.
        val placeholder = ChatMessage(role = ChatRole.ASSISTANT, text = "正在处理…")
        _state.update {
            it.copy(
                // 问卷作答在后台提交：答案不进对话，直接等待结果。
                messages = if (echoUser) it.messages + ChatMessage(role = ChatRole.USER, text = text) + placeholder
                else it.messages + placeholder,
                busy = true,
            )
        }
        viewModelScope.launch {
            try {
                val envelope = agent.streamMessage(
                    principal = principal,
                    sessionToken = sessionToken,
                    conversationId = conversationId,
                    text = text,
                ) { label ->
                    _state.update { state ->
                        state.copy(
                            messages = state.messages.map {
                                if (it.id == placeholder.id) it.copy(text = label) else it
                            },
                        )
                    }
                }
                conversationId = envelope.conversationId.ifBlank { conversationId }
                _state.update { state ->
                    state.copy(
                        busy = false,
                        messages = state.messages.map {
                            if (it.id == placeholder.id) {
                                ChatMessage(
                                    role = ChatRole.ASSISTANT,
                                    text = displayText(envelope.reply, envelope.result),
                                    riskLevel = envelope.riskLevel,
                                    result = envelope.result,
                                    traceId = envelope.requestId,
                                )
                            } else {
                                it
                            }
                        },
                        pending = envelope.approval,
                        otp = "",
                        quiz = parseQuiz(envelope.result),
                        profileResult = envelope.result?.get("profile_result") as? Map<String, Any>,
                        suggestions = (envelope.result?.get("suggestions") as? List<*>)?.mapNotNull { it?.toString() } ?: emptyList(),
                    )
                }
                persistMessages()
            } catch (failure: GatewayException) {
                // Drop the placeholder: leaving "正在处理…" behind after a
                // failure is exactly the kind of lie this codebase avoids.
                _state.update { state -> state.copy(messages = state.messages.filterNot { it.id == placeholder.id }) }
                persistMessages()
                applyFailure(failure)
            } catch (failure: Exception) {
                // A client-side fault must not take the app down; report it the
                // same way a server-side refusal is reported.
                _state.update { state ->
                    state.copy(
                        busy = false,
                        messages = state.messages.filterNot { it.id == placeholder.id },
                        error = "请求失败：${failure.message ?: failure::class.java.simpleName}",
                    )
                }
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
        val approval = _state.value.pending ?: return
        val otp = _state.value.otp
        if (otp.length < 6) {
            _state.update { it.copy(error = "请输入 6 位短信验证码") }
            return
        }
        // Without an enrolled key the signature would be unverifiable, so say so
        // instead of sending an attestation the core is guaranteed to refuse.
        val enrolledDevice = deviceId
        if (enrolledDevice.isNullOrBlank()) {
            _state.update { it.copy(error = "本机尚未完成设备注册，无法完成红色风险验证") }
            return
        }
        val principal = _state.value.principal
        _state.update { it.copy(busy = true, error = null) }
        viewModelScope.launch {
            try {
                val challenge = agent.requestFaceChallenge(principal, sessionToken, approval.approvalId)
                // The private key stays in the Keystore; this returns only a
                // signature over the canonical challenge string.  Signing is
                // Keystore work too, so it happens off the main thread.
                val signature = withContext(Dispatchers.IO) {
                    deviceKeys.signChallenge(
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
                val resolved = agent.resolveApproval(
                    principal = principal,
                    sessionToken = sessionToken,
                    approvalId = approval.approvalId,
                    decision = "approve",
                    otp = otp,
                    faceAttestation = envelope,
                )
                _state.update { state ->
                    state.copy(
                        busy = false,
                        pending = null,
                        otp = "",
                        messages = state.messages + ChatMessage(
                            role = ChatRole.ASSISTANT,
                            text = displayText(resolved.reply, resolved.result),
                            riskLevel = resolved.riskLevel,
                            result = resolved.result,
                        ),
                    )
                }
                persistMessages()
            } catch (failure: GatewayException) {
                applyFailure(failure)
            } catch (failure: Exception) {
                // A Keystore failure is a device problem, not a bank decision;
                // say so instead of blaming the operation.
                _state.update { it.copy(busy = false, error = "设备签名失败：${failure.message ?: failure::class.java.simpleName}") }
            }
        }
    }

    private fun resolve(approval: ApprovalView, decision: String) {
        val principal = _state.value.principal
        _state.update { it.copy(busy = true, error = null) }
        viewModelScope.launch {
            try {
                val envelope = agent.resolveApproval(
                    principal = principal,
                    sessionToken = sessionToken,
                    approvalId = approval.approvalId,
                    decision = decision,
                )
                _state.update { state ->
                    state.copy(
                        busy = false,
                        pending = null,
                        otp = "",
                        quiz = parseQuiz(envelope.result),
                        profileResult = envelope.result?.get("profile_result") as? Map<String, Any>,
                        suggestions = (envelope.result?.get("suggestions") as? List<*>)?.mapNotNull { it?.toString() } ?: emptyList(),
                        messages = state.messages + ChatMessage(
                            role = ChatRole.ASSISTANT,
                            text = displayText(envelope.reply, envelope.result),
                            riskLevel = envelope.riskLevel,
                            result = envelope.result,
                        ),
                    )
                }
                persistMessages()
            } catch (failure: GatewayException) {
                applyFailure(failure)
            }
        }
    }

    private fun applyFailure(failure: GatewayException) {
        _state.update { state ->
            if (failure.sessionExpired) {
                sessionToken = null
                sessionStore.clear()
                ChatUiState(error = "登录状态已失效，请重新登录")
            } else {
                // 审批/验证失败时短信验证码已经被核心消费或拒绝，字段必须清空：
                // 留着它，用户再次点“确认执行”只会把同一个失效的码再送一次。
                state.copy(busy = false, error = failure.message, otp = "")
            }
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
        _state.update { state ->
            if (failure.sessionExpired) {
                sessionToken = null
                sessionStore.clear()
                ChatUiState(error = "登录状态已失效，请重新登录")
            } else {
                state.copy(panelBusy = false, panelError = failure.message, error = failure.message)
            }
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
    }
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
    val deviceId: String?,
    val conversationId: String?,
    val messages: String?,
    val quiz: String?,
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
