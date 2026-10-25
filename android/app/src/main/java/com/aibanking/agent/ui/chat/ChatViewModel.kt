package com.aibanking.agent.ui.chat

import com.aibanking.agent.BuildConfig
import android.app.Application
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.aibanking.agent.data.CardsRepository
import com.aibanking.agent.data.AgentRepository
import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.data.ContactRepository
import com.aibanking.agent.data.DeviceRepository
import com.aibanking.agent.data.GatewayException
import com.aibanking.agent.data.LoginRepository
import com.aibanking.agent.data.SessionCheck
import com.aibanking.agent.data.SessionStore
import com.aibanking.agent.data.TaskRepository
import com.aibanking.agent.data.TurnEnvelope
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
import java.util.UUID

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

    internal val sessionStore = SessionStore(application)
    internal var deviceKeys: DeviceKeyManager? = null
    internal val agent = AgentRepository.create()
    private val login = LoginRepository.create()
    internal val devices = DeviceRepository.create()
    internal val tasks = TaskRepository.create()
    internal val contacts = ContactRepository.create()
    internal val wealth = WealthRepository.create()
    internal val cardsRepo = CardsRepository.create()
    internal val gson = Gson()

    internal val _state = MutableStateFlow(ChatUiState(restoring = true))
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    internal val sessionWork = SessionWork(viewModelScope)

    private var conversationId: String = UUID.randomUUID().toString().replace("-", "").take(20)
    internal var sessionToken: String? = null
    internal var deviceId: String? = null

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
    internal var cardCreateAttempt: CreateAttempt? = null
    internal var contactCreateAttempt: CreateAttempt? = null

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
    internal fun SessionWork.Lease.credential(): String? {
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
            ?.let { replySuggestions(it.result) }
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
    internal fun markUserActivity() {
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
        // This explicit read-only action is independent of generated suggestions
        // and leaves any draft intact when returning to the conversation.
        sendInternal("查一下余额")
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
        val bounded = _state.value.messages.filterNot { it.streaming }.takeLast(MAX_PERSISTED_MESSAGES)
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

    fun dismissPanels() = _state.update { it.copy(showDevices = false, showTasks = false, showContacts = false, showWealth = false, showCards = false, feishuBindingCode = null) }

    /** Exposed so the device panel can mark "this is this phone" honestly. */
    fun currentDeviceId(): String? = deviceId

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
        if (!current.authenticated || current.busy || current.quiz != null || current.input.isNotBlank() ||
            prompt.isEmpty() || prompt !in current.suggestions) return
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
        revealOutcome: Boolean = false,
    ) {
        if (updateConversation) conversationId = envelope.conversationId.ifBlank { conversationId }
        _state.update { state ->
            val previous = state.messages.firstOrNull { it.id == messageId }
            val message = ChatMessage(id = messageId, role = ChatRole.ASSISTANT,
                text = displayText(envelope.response?.summary ?: envelope.reply, envelope.displayResult),
                riskLevel = envelope.riskLevel, approvalPreview = envelope.approval != null && envelope.result?.get("side_inquiry") != true,
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
            // An approved operation that reached a result opens its result page at once.
            val reveal = revealOutcome && envelope.approval == null && isTaskOutcome(message)
            state.copy(busy = false, pending = envelope.approval, otp = "", activeStages = emptyList(),
                outcomeMessageId = if (reveal) messageId else state.outcomeMessageId,
                lastProblem = if (clearProblem) null else state.lastProblem,
                showProblem = if (clearProblem) false else state.showProblem,
                messages = messages, quiz = parseQuiz(envelope.displayResult),
                profileResult = envelope.displayResult?.get("profile_result") as? Map<*, *>,
                suggestions = predictedSuggestions(envelope))
        }
        persistMessages()
        if (envelope.businessStatus == "PENDING_RECONCILIATION") reconcileMessage(messageId)
        if (updateConversation && envelope.approval == null) refreshSuggestions()
        if (envelope.approval?.mfaRequired == true) autoFillDemoOtp()
    }

    /**
     * Show independently reviewed follow-ups once they arrive. The reply is never held for
     * them; a newer turn, an approval or the quiz keeps the chips it already has.
     */
    private fun refreshSuggestions() {
        val revision = interactionRevision
        val principal = _state.value.principal
        val conversation = conversationId
        sessionWork.launch {
            val predicted = try {
                io { agent.suggestions(principal, credential(), conversation) }
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                null
            }
            // An authoritative empty result removes rejected examples. A
            // transport failure keeps the safe examples already on screen.
            val chips = predicted?.let(::cleanSuggestions) ?: return@launch
            _state.update { state ->
                if (revision != interactionRevision || state.busy || state.pending != null || state.quiz != null) state
                else state.copy(suggestions = chips)
            }
        }
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

    internal fun sendInternal(text: String, echoUser: Boolean = true) {
        markUserActivity()
        val current = _state.value
        val principal = current.principal
        if (!current.authenticated) return
        interactionRevision++
        // A placeholder assistant bubble carries the live stage label and is then
        // replaced by the real answer, so progress appears where the reply will.
        val placeholder = ChatMessage(role = ChatRole.ASSISTANT, text = "正在处理…", streaming = true)
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
                applyEnvelope(resolved, revealOutcome = true)
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

    /** Fetch and fill the demo code for a red approval; the demo sends no real SMS. */
    private fun autoFillDemoOtp() {
        if (BuildConfig.AUTO_FILL_DEMO_OTP && _state.value.otp.isEmpty()) requestDemoOtp()
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
                    // Filled in like an SMS autofill, so the red flow is: get code, verify, fingerprint.
                    _state.update { it.copy(busy = false, otp = code, notice = "已填入本机演示验证码 $code，有效 ${result.expiresIn} 秒；未发送短信") }
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
                applyEnvelope(envelope, revealOutcome = decision == "approve")
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
        // A rejected code is consumed; the card still waits, so fill a fresh one.
        if (failure.code == "MFA_FAILED" && _state.value.pending?.mfaRequired == true) autoFillDemoOtp()
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
    internal fun applyPanelFailure(failure: GatewayException) {
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
    internal fun applyPanelFailure(failure: Exception) {
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
internal class CreateAttempt(val fingerprint: String, val value: String) {
    companion object {
        fun of(previous: CreateAttempt?, fingerprint: String): CreateAttempt =
            if (previous != null && previous.fingerprint == fingerprint) {
                previous
            } else {
                CreateAttempt(fingerprint, UUID.randomUUID().toString())
            }
    }
}
