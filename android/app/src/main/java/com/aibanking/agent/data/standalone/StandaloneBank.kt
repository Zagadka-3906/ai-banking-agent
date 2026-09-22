package com.aibanking.agent.data.standalone

import android.content.Context
import android.content.SharedPreferences
import com.aibanking.agent.data.*
import com.aibanking.agent.security.DeviceKeyManager
import com.google.gson.Gson
import com.google.gson.JsonParser
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody
import okhttp3.ResponseBody.Companion.toResponseBody
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Base64
import java.util.UUID
import kotlin.math.round

/**
 * Self-contained banking sandbox used by the normal APK.
 *
 * It implements the exact Retrofit contracts used by the network build, which
 * keeps UI behavior and authorization boundaries identical while removing the
 * emulator's dependency on a computer listening on 10.0.2.2. All data is mock
 * data stored in this app's private SharedPreferences; no real payment rail is
 * contacted.
 */
object StandaloneBank {
    private const val PREFS = "standalone_bank_v1"
    private const val SNAPSHOT = "snapshot"
    private const val STATE_SCHEMA = 2
    private const val DEMO_PASSWORD = "Bank@2026"
    private const val OTP = "888888"
    private val gson = Gson()
    private val lock = Any()
    private var preferences: SharedPreferences? = null
    private var state: LocalState = defaultState()
    private val approvals = mutableMapOf<String, PendingApproval>()
    private val challenges = mutableMapOf<String, LocalChallenge>()
    private val messageResults = mutableMapOf<String, TurnEnvelope>()
    private val createResults = mutableMapOf<String, Any>()

    fun initialize(context: Context) = synchronized(lock) {
        preferences = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        state = preferences?.getString(SNAPSHOT, null)?.let { raw ->
            runCatching { gson.fromJson(raw, LocalState::class.java) }.getOrNull()
        }?.takeIf { it.schema == STATE_SCHEMA } ?: defaultState()
        normalizeState()
        save()
    }

    /** A deterministic clean state for local JVM tests; production never calls it. */
    internal fun resetForTests() = synchronized(lock) {
        preferences = null
        state = defaultState()
        approvals.clear()
        challenges.clear()
        messageResults.clear()
        createResults.clear()
    }

    val agentApi: AgentApi = object : AgentApi {
        override suspend fun sendMessage(body: MessageRequest, userId: String, sessionToken: String?) =
            this@StandaloneBank.sendMessage(body, userId, sessionToken)
        override suspend fun sendMessageStream(body: MessageRequest, userId: String, sessionToken: String?) =
            this@StandaloneBank.sendMessageStream(body, userId, sessionToken)
        override suspend fun resolveApproval(approvalId: String, body: ApprovalRequest, userId: String, sessionToken: String?) =
            this@StandaloneBank.resolveApproval(approvalId, body, userId, sessionToken)
        override suspend fun faceChallenge(approvalId: String, userId: String, sessionToken: String?) =
            this@StandaloneBank.faceChallenge(approvalId, userId, sessionToken)
    }

    val sessionApi: SessionApi = object : SessionApi {
        override suspend fun login(body: LoginRequest) = this@StandaloneBank.login(body)
        override suspend fun logout(sessionToken: String) = this@StandaloneBank.logout(sessionToken)
        override suspend fun identity(sessionToken: String) = this@StandaloneBank.identity(sessionToken)
    }

    val deviceApi: DeviceApi = object : DeviceApi {
        override suspend fun register(body: DeviceRegisterRequest, userId: String, sessionToken: String) =
            this@StandaloneBank.register(body, userId, sessionToken)
        override suspend fun registerPushToken(deviceId: String, body: PushTokenRequest, userId: String, sessionToken: String) =
            this@StandaloneBank.registerPushToken(deviceId, body, userId, sessionToken)
        override suspend fun list(userId: String, sessionToken: String) = this@StandaloneBank.deviceList(userId, sessionToken)
        override suspend fun revoke(deviceId: String, userId: String, sessionToken: String) =
            this@StandaloneBank.revoke(deviceId, userId, sessionToken)
    }

    val contactApi: ContactApi = object : ContactApi {
        override suspend fun list(userId: String, userIdHeader: String, sessionToken: String) =
            this@StandaloneBank.contactList(userId, userIdHeader, sessionToken)
        override suspend fun add(userId: String, body: ContactAddRequest, userIdHeader: String, sessionToken: String, idempotencyKey: String) =
            this@StandaloneBank.contactAdd(userId, body, userIdHeader, sessionToken, idempotencyKey)
        override suspend fun remove(userId: String, contactId: String, userIdHeader: String, sessionToken: String) =
            this@StandaloneBank.remove(userId, contactId, userIdHeader, sessionToken)
    }

    val cardsApi: CardsApi = object : CardsApi {
        override suspend fun list(userId: String, userIdHeader: String, sessionToken: String) =
            this@StandaloneBank.cardList(userId, userIdHeader, sessionToken)
        override suspend fun add(userId: String, body: CardAddRequest, userIdHeader: String, sessionToken: String, idempotencyKey: String) =
            this@StandaloneBank.cardAdd(userId, body, userIdHeader, sessionToken, idempotencyKey)
        override suspend fun reveal(userId: String, cardId: String, body: CardRevealRequest, userIdHeader: String, sessionToken: String) =
            this@StandaloneBank.reveal(userId, cardId, body, userIdHeader, sessionToken)
    }

    val taskApi: TaskApi = object : TaskApi {
        override suspend fun list(userId: String, sessionToken: String) = this@StandaloneBank.taskList(userId, sessionToken)
        override suspend fun cancel(taskId: String, body: TaskActionRequest, userId: String, sessionToken: String) =
            this@StandaloneBank.cancel(taskId, body, userId, sessionToken)
    }

    val wealthApi: WealthApi = object : WealthApi {
        override suspend fun products(userId: String, sessionToken: String) = this@StandaloneBank.products(userId, sessionToken)
        override suspend fun holdings(userId: String, userIdHeader: String, sessionToken: String) =
            this@StandaloneBank.holdings(userId, userIdHeader, sessionToken)
        override suspend fun riskProfile(userId: String, sessionToken: String) = this@StandaloneBank.riskProfile(userId, sessionToken)
    }

    private suspend fun login(body: LoginRequest): SessionResponse = synchronized(lock) {
        val principal = body.userId.trim()
        if (principal !in state.balances || body.password != DEMO_PASSWORD) {
            audit(principal.ifBlank { "unknown" }, "LOGIN", "DENIED")
            throw GatewayException("INVALID_CREDENTIALS", "用户标识或登录口令错误", 401)
        }
        val token = "local_${UUID.randomUUID()}"
        val expiresAt = epochSeconds() + 30L * 24 * 3600
        state.sessions[token] = LocalSession(principal, expiresAt)
        audit(principal, "LOGIN", "SUCCEEDED")
        save()
        SessionResponse(200, token, principal, expiresAt, 30 * 24 * 3600)
    }

    private suspend fun logout(sessionToken: String): LogoutResponse = synchronized(lock) {
        val principal = state.sessions.remove(sessionToken)?.userId
        if (principal != null) audit(principal, "LOGOUT", "SUCCEEDED")
        save()
        LogoutResponse(200, "LOGGED_OUT", principal)
    }

    private suspend fun identity(sessionToken: String): IdentityResponse = synchronized(lock) {
        val subject = session(sessionToken)
        IdentityResponse(200, subject)
    }

    private suspend fun sendMessage(body: MessageRequest, userId: String, sessionToken: String?): TurnEnvelope =
        synchronized(lock) { processMessage(body, userId, sessionToken) }

    private suspend fun sendMessageStream(body: MessageRequest, userId: String, sessionToken: String?): ResponseBody {
        val envelope = synchronized(lock) { processMessage(body, userId, sessionToken) }
        val terminal = if (envelope.approvalRequired) "approval" else "result"
        val stream = buildString {
            append("event: stage\n")
            append("data: ").append(gson.toJson(mapOf("stage" to "understand", "label" to "理解您的需求"))).append("\n\n")
            append("event: stage\n")
            append("data: ").append(gson.toJson(mapOf("stage" to "verify", "label" to "核对本地账本"))).append("\n\n")
            append("event: ").append(terminal).append("\n")
            append("data: ").append(gson.toJson(envelope)).append("\n\n")
        }
        return stream.toResponseBody("text/event-stream; charset=utf-8".toMediaType())
    }

    private suspend fun resolveApproval(
        approvalId: String,
        body: ApprovalRequest,
        userId: String,
        sessionToken: String?,
    ): TurnEnvelope = synchronized(lock) {
        authenticate(userId, sessionToken)
        val pending = approvals.remove(approvalId)
            ?: throw GatewayException("APPROVAL_NOT_FOUND", "确认请求已失效，请重新发起", 404)
        if (pending.userId != userId || pending.expiresAt < epochSeconds()) {
            audit(userId, pending.action, "DENIED_EXPIRED")
            throw GatewayException("APPROVAL_EXPIRED", "确认请求已过期，请重新发起", 409)
        }
        if (!body.decision.equals("APPROVE", true)) {
            audit(userId, pending.action, "REJECTED")
            return@synchronized completed(
                pending.conversationId,
                pending.intent,
                "已取消，本地账本未发生变更。",
                mapOf("execution_result" to mapOf("type" to "CANCELLED", "data" to mapOf("status" to "CANCELLED"))),
            )
        }
        if (pending.risk == "RED") {
            if (body.otp != OTP) {
                audit(userId, pending.action, "DENIED_OTP")
                recordSecurityFailure(userId)
                throw GatewayException("MFA_INVALID", "短信验证码错误", 403)
            }
            try {
                verifyAttestation(userId, approvalId, body.faceAttestation)
            } catch (failure: GatewayException) {
                recordSecurityFailure(userId)
                throw failure
            }
        }
        execute(pending).also {
            state.securityFailures.remove(userId)
            audit(userId, pending.action, "SUCCEEDED")
            save()
        }
    }

    private suspend fun faceChallenge(approvalId: String, userId: String, sessionToken: String?): FaceChallengeView =
        synchronized(lock) {
            authenticate(userId, sessionToken)
            val pending = approvals[approvalId]
                ?: throw GatewayException("APPROVAL_NOT_FOUND", "确认请求已失效，请重新发起", 404)
            if (pending.userId != userId || pending.risk != "RED") {
                throw GatewayException("FORBIDDEN", "该操作不能申请设备验证", 403)
            }
            val id = "challenge_${UUID.randomUUID()}"
            val jti = "auth_${UUID.randomUUID()}"
            challenges[id] = LocalChallenge(id, approvalId, userId, jti, epochSeconds() + 120)
            FaceChallengeView(200, approvalId, id, jti, 120)
        }

    private suspend fun register(body: DeviceRegisterRequest, userId: String, sessionToken: String): DeviceView =
        synchronized(lock) {
            authenticate(userId, sessionToken)
            runCatching { KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(Base64.getDecoder().decode(body.publicKey))) }
                .getOrElse { throw GatewayException("INVALID_DEVICE_KEY", "设备公钥格式无效", 400) }
            val fingerprint = sha256(body.publicKey).take(32)
            val existing = state.devices.firstOrNull { it.userId == userId && it.fingerprint == fingerprint }
            val stored = existing?.apply {
                publicKey = body.publicKey
                status = "ACTIVE"
                lastSeenAt = epochSeconds()
            } ?: StoredDevice(
                deviceId = "DEV-${UUID.randomUUID().toString().take(8).uppercase()}",
                userId = userId,
                publicKey = body.publicKey,
                fingerprint = fingerprint,
                status = "ACTIVE",
                label = body.label,
                createdAt = epochSeconds(),
                lastSeenAt = epochSeconds(),
            ).also(state.devices::add)
            audit(userId, "DEVICE_REGISTER", "SUCCEEDED")
            save()
            stored.view()
        }

    private suspend fun registerPushToken(
        deviceId: String,
        body: PushTokenRequest,
        userId: String,
        sessionToken: String,
    ): DeviceView = synchronized(lock) {
        authenticate(userId, sessionToken)
        val device = ownedDevice(deviceId, userId).apply { pushEnabled = body.token.isNotBlank() }
        save()
        device.view()
    }

    private suspend fun deviceList(userId: String, sessionToken: String): DeviceListResponse = synchronized(lock) {
        authenticate(userId, sessionToken)
        DeviceListResponse(200, state.devices.filter { it.userId == userId }.map { it.view() })
    }

    private suspend fun revoke(deviceId: String, userId: String, sessionToken: String): DeviceView = synchronized(lock) {
        authenticate(userId, sessionToken)
        val device = ownedDevice(deviceId, userId).apply { status = "REVOKED"; lastSeenAt = epochSeconds() }
        audit(userId, "DEVICE_REVOKE", "SUCCEEDED")
        save()
        device.view()
    }

    private suspend fun contactList(userId: String, userIdHeader: String, sessionToken: String): ContactListResponse =
        synchronized(lock) {
            authenticate(userIdHeader, sessionToken); requireOwner(userId, userIdHeader)
            ContactListResponse(200, state.contacts.filter { it.ownerId == userId }.map { it.view })
        }

    private suspend fun contactAdd(
        userId: String,
        body: ContactAddRequest,
        userIdHeader: String,
        sessionToken: String,
        idempotencyKey: String,
    ): ContactAddResponse = synchronized(lock) {
        authenticate(userIdHeader, sessionToken); requireOwner(userId, userIdHeader)
        val cacheKey = "contact:$userId:$idempotencyKey"
        (createResults[cacheKey] as? ContactAddResponse)?.let { return@synchronized it }
        val name = body.name.trim()
        val phone = body.phone.filter(Char::isDigit)
        if (name.isBlank() || phone.length !in 5..20) throw GatewayException("INVALID_CONTACT", "请填写有效的姓名和手机号", 400)
        val relation = body.relationship.ifBlank { "OTHER" }.uppercase()
        val view = ContactView(
            contactId = "CONTACT-${UUID.randomUUID().toString().take(8).uppercase()}",
            name = name,
            phone = phone,
            relationship = relation,
            relationshipLabel = relationLabel(relation),
            birthday = body.birthday,
            accountId = when (phone) { "13800138001" -> "user_li"; "13800138002" -> "user_wife"; else -> "payee_${phone.takeLast(8)}" },
            linkedExisting = phone in setOf("13800138001", "13800138002"),
            accountName = name,
            createdAt = now(),
        )
        state.contacts.add(OwnedContact(userId, view))
        val response = ContactAddResponse(200, "CREATED", view)
        createResults[cacheKey] = response
        audit(userId, "CONTACT_ADD", "SUCCEEDED")
        save()
        response
    }

    private suspend fun remove(
        userId: String,
        contactId: String,
        userIdHeader: String,
        sessionToken: String,
    ): ContactRemoveResponse = synchronized(lock) {
        authenticate(userIdHeader, sessionToken); requireOwner(userId, userIdHeader)
        val removed = state.contacts.removeAll { it.ownerId == userId && it.view.contactId == contactId }
        if (!removed) throw GatewayException("CONTACT_NOT_FOUND", "联系人不存在", 404)
        audit(userId, "CONTACT_REMOVE", "SUCCEEDED")
        save()
        ContactRemoveResponse(200, "REMOVED")
    }

    private suspend fun cardList(userId: String, userIdHeader: String, sessionToken: String): CardListResponse =
        synchronized(lock) {
            authenticate(userIdHeader, sessionToken); requireOwner(userId, userIdHeader)
            val balance = state.balances[userId] ?: 0.0
            CardListResponse(200, state.cards.filter { it.ownerId == userId }.map { it.view.copy(availableBalance = balance) })
        }

    private suspend fun cardAdd(
        userId: String,
        body: CardAddRequest,
        userIdHeader: String,
        sessionToken: String,
        idempotencyKey: String,
    ): CardAddResponse = synchronized(lock) {
        authenticate(userIdHeader, sessionToken); requireOwner(userId, userIdHeader)
        val cacheKey = "card:$userId:$idempotencyKey"
        (createResults[cacheKey] as? CardAddResponse)?.let { return@synchronized it }
        val number = body.card_number.filter(Char::isDigit)
        if (number.length !in 13..19 || !luhn(number)) throw GatewayException("INVALID_CARD_NUMBER", "银行卡号校验失败", 400)
        if (state.cards.any { it.ownerId == userId && it.fullNumber == number }) throw GatewayException("CARD_ALREADY_EXISTS", "这张卡已经添加", 409)
        val id = "CARD-${(state.cards.size + 1).toString().padStart(3, '0')}"
        val view = CardView(
            cardId = id,
            numberMasked = "${number.take(4)} **** **** ${number.takeLast(4)}",
            last4 = number.takeLast(4), type = body.card_type, status = "ACTIVE",
            dailyLimit = 5000.0, dailySpentToday = 0.0, dailyRemaining = 5000.0,
            availableBalance = state.balances[userId] ?: 0.0, label = body.label ?: "我的银行卡",
        )
        state.cards.add(OwnedCard(userId, view, number))
        val response = CardAddResponse(200, "CREATED", view)
        createResults[cacheKey] = response
        audit(userId, "CARD_ADD", "SUCCEEDED")
        save()
        response
    }

    private suspend fun reveal(
        userId: String,
        cardId: String,
        body: CardRevealRequest,
        userIdHeader: String,
        sessionToken: String,
    ): CardRevealResponse = synchronized(lock) {
        authenticate(userIdHeader, sessionToken); requireOwner(userId, userIdHeader)
        if (body.pin != "24681012") throw GatewayException("PIN_INVALID", "验证失败，请检查取款密码", 403)
        val card = state.cards.firstOrNull { it.ownerId == userId && it.view.cardId == cardId }
            ?: throw GatewayException("CARD_NOT_FOUND", "银行卡不存在", 404)
        audit(userId, "CARD_REVEAL", "SUCCEEDED")
        CardRevealResponse(200, "REVEALED", CardRevealView("张三", card.fullNumber, "招商银行", "12/31", "DISC-${UUID.randomUUID().toString().take(8)}"))
    }

    private suspend fun taskList(userId: String, sessionToken: String): TaskListResponse = synchronized(lock) {
        authenticate(userId, sessionToken)
        TaskListResponse(200, state.tasks.filter { it.ownerId == userId }.map { it.view })
    }

    private suspend fun cancel(
        taskId: String,
        body: TaskActionRequest,
        userId: String,
        sessionToken: String,
    ): TaskActionResponse = synchronized(lock) {
        authenticate(userId, sessionToken)
        val index = state.tasks.indexOfFirst { it.ownerId == userId && it.view.taskId == taskId }
        if (index < 0) throw GatewayException("TASK_NOT_FOUND", "任务不存在", 404)
        val current = state.tasks[index]
        if (current.view.status !in setOf("PENDING", "WAITING_USER")) throw GatewayException("TASK_NOT_CANCELLABLE", "当前任务不能取消", 409)
        val updated = current.copy(view = current.view.copy(status = "CANCELLED", updatedAt = now()))
        state.tasks[index] = updated
        audit(userId, "TASK_CANCEL", "SUCCEEDED")
        save()
        TaskActionResponse(200, updated.view)
    }

    private suspend fun products(userId: String, sessionToken: String): ProductListResponse = synchronized(lock) {
        authenticate(userId, sessionToken)
        ProductListResponse(200, productsFixture)
    }

    private suspend fun holdings(userId: String, userIdHeader: String, sessionToken: String): HoldingListResponse =
        synchronized(lock) {
            authenticate(userIdHeader, sessionToken); requireOwner(userId, userIdHeader)
            HoldingListResponse(200, state.holdings.filter { it.ownerId == userId }.map { it.view })
        }

    private suspend fun riskProfile(userId: String, sessionToken: String): RiskProfileResponse = synchronized(lock) {
        authenticate(userId, sessionToken)
        RiskProfileResponse(200, state.riskProfiles[userId]?.let { RiskProfileView(it, LocalDate.now().toString(), "LOCAL_ASSESSMENT") })
    }

    private fun processMessage(body: MessageRequest, userId: String, sessionToken: String?): TurnEnvelope {
        authenticate(userId, sessionToken)
        val messageId = body.messageId?.takeIf(String::isNotBlank)
        messageId?.let { messageResults[it] }?.let { return it }
        val text = body.text.trim()
        if (text.isBlank()) throw GatewayException("EMPTY_MESSAGE", "请输入要办理的事项", 400)
        val conversation = body.conversationId?.takeIf(String::isNotBlank) ?: "local_conversation_${UUID.randomUUID()}"
        val envelope = route(text, userId, conversation)
        messageId?.let { messageResults[it] = envelope }
        audit(userId, envelope.intent ?: "UNKNOWN", if (envelope.approvalRequired) "AWAITING_APPROVAL" else "COMPLETED")
        save()
        return envelope
    }

    private fun route(text: String, userId: String, conversation: String): TurnEnvelope {
        val normalized = text.lowercase().replace(" ", "")
        if (looksLikeInjection(normalized)) {
            recordSecurityFailure(userId)
            return completed(
                conversation, "SECURITY_GUARD",
                "我不能绕过身份、授权或审计规则。您可以直接说明要查询或办理的银行业务。",
                mapOf("security" to mapOf("status" to "BLOCKED", "reason" to "PROMPT_INJECTION")),
            )
        }
        if (normalized == "退出测评") return completed(conversation, "WEALTH_RISK_ASSESSMENT", "已退出风险测评，未修改您的风险档案。")
        if (Regex("""(?:^|\s)1[A-D].*8[A-D]""", RegexOption.IGNORE_CASE).containsMatchIn(text)) {
            state.riskProfiles[userId] = "C3"
            return completed(
                conversation, "WEALTH_RISK_ASSESSMENT", "✅ 风险测评完成：C3 稳健型\n特点：能承受一定净值波动。\n匹配理财产品级别：R1-R3",
                mapOf(
                    "profile_result" to mapOf("grade" to "C3", "type" to "稳健型", "traits" to "能承受一定净值波动", "matching_levels" to "R1-R3"),
                    "suggestions" to listOf("查看适合我的理财", "比较理财产品", "查看我的持仓"),
                ),
            )
        }
        if (normalized.contains("风险测评") || normalized.contains("风险评估")) {
            return completed(
                conversation, "WEALTH_RISK_ASSESSMENT", "请完成以下 8 道风险测评题，选择最符合您的选项。",
                mapOf("risk_quiz" to mapOf("active" to true, "questions" to riskQuestions())),
            )
        }
        if (normalized.contains("境外") && (normalized.contains("不是我的") || normalized.contains("异常") || normalized.contains("盗刷"))) {
            return approval(userId, conversation, "FRAUD_DISPUTE", "FRAUD_DISPUTE", "核验境外异常消费并保护关联卡片（红色风险）", "RED")
        }
        if (normalized.contains("aa") || normalized.contains("分摊") || normalized.contains("平摊")) {
            val total = extractAmount(text) ?: return completed(conversation, "SPLIT_BILL", "请补充分摊总金额和参与人数，例如“600 元 3 人 AA”。")
            val people = Regex("""(\d{1,2})\s*(?:人|位)""").find(text)?.groupValues?.get(1)?.toIntOrNull() ?: 2
            if (people < 2) return completed(conversation, "SPLIT_BILL", "AA 收款至少需要 2 人参与。")
            return approval(userId, conversation, "SPLIT_BILL", "SPLIT_BILL", "发起 ${money(total)}、共 $people 人的 AA 收款", "YELLOW", mapOf("amount" to total, "people" to people))
        }
        if (normalized.contains("预约") || normalized.contains("定时") || normalized.contains("明天转") || normalized.contains("下周转")) {
            val amount = extractAmount(text)
            val recipient = extractRecipient(text)
            if (amount == null || recipient == null) return completed(conversation, "SCHEDULED_TRANSFER", "请补充收款人、金额和执行时间，例如“明天给李四转 300 元”。")
            return approval(userId, conversation, "SCHEDULED_TRANSFER", "SCHEDULED_TRANSFER", "预约向 $recipient 转账 ${money(amount)}；到期前仍需再次确认", "YELLOW", mapOf("amount" to amount, "recipient" to recipient))
        }
        if (isTransfer(normalized)) return transferTurn(text, normalized, userId, conversation)
        if (normalized.contains("取消") && (normalized.contains("订阅") || normalized.contains("apple") || normalized.contains("会员"))) {
            val merchant = if (normalized.contains("视频")) "视频会员" else "Apple 订阅"
            return approval(userId, conversation, "SUBSCRIPTION_CANCEL", "SUBSCRIPTION_CANCEL", "取消 $merchant", "YELLOW", mapOf("merchant" to merchant))
        }
        if (normalized.contains("订阅") || normalized.contains("代扣") || normalized.contains("续费") || normalized.contains("扣费")) return subscriptionQuery(userId, conversation)
        if (normalized.contains("申购") || normalized.contains("购买理财")) return wealthPurchase(text, userId, conversation)
        if (normalized.contains("赎回")) return wealthRedeem(text, userId, conversation)
        if (normalized.contains("持仓")) return holdingsQuery(userId, conversation)
        if (normalized.contains("理财") || normalized.contains("产品") || normalized.contains("投资")) return wealthQuery(conversation)
        if (normalized.contains("账单") || normalized.contains("消费") || normalized.contains("花哪") || normalized.contains("开销")) return billQuery(conversation)
        if (normalized.contains("余额") || normalized.contains("有多少钱") || normalized.contains("还有多少")) return balanceQuery(userId, conversation)
        if (normalized.contains("生日") || normalized.contains("鲜花") || normalized.contains("蛋糕") || normalized.contains("锁定")) {
            val amount = extractAmount(text) ?: 1000.0
            return approval(userId, conversation, "CROSS_SCENE", "CROSS_SCENE", "为爱人生日锁定 ${money(amount)}，并在生日前 2 天准备鲜花和蛋糕", if (amount > 1000) "RED" else "YELLOW", mapOf("amount" to amount))
        }
        if (normalized.contains("卡")) {
            val action = when {
                normalized.contains("解挂") -> "CARD_UNREPORT_LOST"
                normalized.contains("挂失") -> "CARD_REPORT_LOST"
                normalized.contains("解冻") || normalized.contains("解除限制") -> "CARD_UNFREEZE"
                normalized.contains("冻结") || normalized.contains("限制") -> "CARD_FREEZE"
                normalized.contains("额度") || normalized.contains("限额") -> "CARD_LIMIT"
                else -> null
            }
            if (action != null) return approval(userId, conversation, action, action, cardActionSummary(action, text), "RED", mapOf("amount" to (extractAmount(text) ?: 5000.0)))
            return cardQuery(userId, conversation)
        }
        return completed(
            conversation, "CAPABILITIES",
            "我可以帮您查询余额和账单、转账、管理理财和卡片、取消订阅，或安排生日预算。请告诉我对象、金额或时间；涉及执行时我会先请您确认。",
        )
    }

    private fun transferTurn(text: String, normalized: String, userId: String, conversation: String): TurnEnvelope {
        val previous = state.lastTransfers[userId]
        val repeat = normalized.contains("再转") || normalized.contains("再来一次")
        val amount = extractAmount(text) ?: if (repeat) previous?.amount else null
        val recipient = extractRecipient(text) ?: if (repeat) previous?.recipient else null
        if (amount == null || recipient.isNullOrBlank()) {
            return completed(conversation, "TRANSFER", "请补充收款人和金额，例如“给李四转 300 元”。我不会在信息不完整时执行转账。")
        }
        if (amount <= 0 || amount > 100_000) return completed(conversation, "TRANSFER", "转账金额需大于 0 元且不超过单笔 100,000 元。")
        if ((state.balances[userId] ?: 0.0) - (state.locked[userId] ?: 0.0) < amount) return completed(conversation, "TRANSFER", "可用余额不足，未创建转账确认。")
        state.lastTransfers[userId] = LastTransfer(recipient, amount)
        val risk = if (amount > 1000) "RED" else "YELLOW"
        return approval(userId, conversation, "TRANSFER", "TRANSFER", "向 $recipient 转账 ${money(amount)}", risk, mapOf("recipient" to recipient, "amount" to amount))
    }

    private fun approval(
        userId: String,
        conversation: String,
        intent: String,
        action: String,
        summary: String,
        risk: String,
        details: Map<String, Any> = emptyMap(),
    ): TurnEnvelope {
        val id = "approval_${UUID.randomUUID()}"
        approvals[id] = PendingApproval(id, userId, conversation, intent, action, risk, details, epochSeconds() + 110)
        return TurnEnvelope(
            code = 200, conversationId = conversation, status = TurnEnvelope.STATUS_APPROVAL_REQUIRED,
            reply = "请确认：$summary。", requestId = requestId(), intent = intent, riskLevel = risk,
            approval = ApprovalView(
                approvalId = id, riskLevel = risk,
                requiredActions = if (risk == "RED") listOf("CONFIRM", "OTP", "DEVICE_SIGNATURE") else listOf("CONFIRM"),
                summary = summary, mfaRequired = risk == "RED", expiresIn = 110,
            ),
        )
    }

    private fun execute(pending: PendingApproval): TurnEnvelope = when (pending.action) {
        "TRANSFER" -> executeTransfer(pending)
        "SUBSCRIPTION_CANCEL" -> executeSubscriptionCancel(pending)
        "FRAUD_DISPUTE" -> executeFraud(pending)
        "WEALTH_PURCHASE" -> executePurchase(pending)
        "WEALTH_REDEEM" -> executeRedeem(pending)
        "CROSS_SCENE" -> executeCrossScene(pending)
        "SCHEDULED_TRANSFER" -> executeScheduledTransfer(pending)
        "SPLIT_BILL" -> executeSplitBill(pending)
        "CARD_FREEZE", "CARD_UNFREEZE", "CARD_REPORT_LOST", "CARD_UNREPORT_LOST", "CARD_LIMIT" -> executeCardAction(pending)
        else -> completed(pending.conversationId, pending.intent, "操作已完成。")
    }

    private fun executeScheduledTransfer(pending: PendingApproval): TurnEnvelope {
        val taskId = "TASK-${UUID.randomUUID().toString().take(8).uppercase()}"
        val amount = pending.details.number("amount")
        val recipient = pending.details["recipient"].toString()
        val task = TaskView(
            taskId, "SCHEDULED_TRANSFER", "WAITING_USER", LocalDate.now().plusDays(1).toString(), now(), now(),
            payload = mapOf("recipient" to recipient, "amount" to amount, "requires_fresh_approval" to true),
        )
        state.tasks.add(OwnedTask(pending.userId, task))
        return completed(
            pending.conversationId, pending.intent, "预约已创建；到期时会等待您再次确认，不会自动扣款。",
            mapOf("execution_result" to mapOf("type" to "SCHEDULED_TRANSFER", "data" to mapOf("status" to "SCHEDULED", "task_id" to taskId, "target" to recipient, "amount" to amount, "scheduled_at" to task.runAt.orEmpty()))),
        )
    }

    private fun executeSplitBill(pending: PendingApproval): TurnEnvelope {
        val splitId = "SPLIT-${UUID.randomUUID().toString().take(8).uppercase()}"
        val total = pending.details.number("amount")
        val people = (pending.details["people"] as? Number)?.toInt()?.coerceAtLeast(2) ?: 2
        val perPerson = roundMoney(total / people)
        return completed(
            pending.conversationId, pending.intent, "AA 收款已创建，共 $people 人，每人 ${money(perPerson)}。这是本地演示请求，不会向真实联系人扣款。",
            mapOf("execution_result" to mapOf("type" to "SPLIT_BILL", "data" to mapOf("status" to "PENDING_COLLECTION", "split_id" to splitId, "amount" to total, "participants_total" to people, "per_person" to perPerson, "collected_amount" to 0.0, "outstanding_amount" to total))),
        )
    }

    private fun executeTransfer(pending: PendingApproval): TurnEnvelope {
        val amount = pending.details.number("amount")
        val recipient = pending.details["recipient"].toString()
        val current = state.balances[pending.userId] ?: 0.0
        if (current - (state.locked[pending.userId] ?: 0.0) < amount) throw GatewayException("INSUFFICIENT_FUNDS", "可用余额不足，转账未执行", 409)
        val remaining = roundMoney(current - amount)
        state.balances[pending.userId] = remaining
        val txId = "TXN${LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMddHHmmss"))}${UUID.randomUUID().toString().take(4).uppercase()}"
        state.transactions.add(LocalTransaction(txId, pending.userId, recipient, "转账", amount, now()))
        return completed(
            pending.conversationId, pending.intent,
            "转账结算成功。流水号 $txId，收款人 $recipient，金额 ${money(amount)}，当前余额 ${money(remaining)}。",
            mapOf("execution_result" to mapOf("type" to "TRANSFER_SUCCESS", "data" to mapOf("status" to "SUCCESS", "transaction_id" to txId, "receiver_name" to recipient, "transferred_amount" to amount, "sender_balance" to remaining))),
        )
    }

    private fun executeSubscriptionCancel(pending: PendingApproval): TurnEnvelope {
        val merchant = pending.details["merchant"].toString()
        val subscription = state.subscriptions.firstOrNull { it.ownerId == pending.userId && it.merchant == merchant && it.status == "ACTIVE" }
            ?: throw GatewayException("SUBSCRIPTION_NOT_FOUND", "没有找到生效中的 $merchant", 404)
        subscription.status = "CANCELLED"
        return completed(
            pending.conversationId, pending.intent, "$merchant 已取消，后续不会在本地沙箱中续费。",
            mapOf("execution_result" to mapOf("type" to "SUBSCRIPTION_CANCEL", "data" to mapOf("status" to "CANCELLED", "merchant" to merchant, "subscription_id" to subscription.subscriptionId))),
        )
    }

    private fun executeFraud(pending: PendingApproval): TurnEnvelope {
        val cardIndex = state.cards.indexOfFirst { it.ownerId == pending.userId }
        val card = state.cards[cardIndex]
        state.cards[cardIndex] = card.copy(view = card.view.copy(status = "FROZEN"))
        val caseId = "CASE-${UUID.randomUUID().toString().take(8).uppercase()}"
        return completed(
            pending.conversationId, pending.intent,
            "关联卡片已冻结，境外异常消费争议工单已提交人工审核；当前未执行退款。",
            mapOf("execution_result" to mapOf("type" to "FRAUD_DISPUTE", "data" to mapOf("data" to mapOf(
                "status" to "CARD_PROTECTED",
                "card" to mapOf("card_id" to card.view.cardId, "status" to "FROZEN", "number_masked" to card.view.numberMasked),
                "dispute_case" to mapOf("case_id" to caseId, "status" to "PENDING_REVIEW"),
                "transaction" to mapOf("merchant" to "境外商户", "amount" to 688.0),
            )))),
        )
    }

    private fun executeCardAction(pending: PendingApproval): TurnEnvelope {
        val index = state.cards.indexOfFirst { it.ownerId == pending.userId }
        if (index < 0) throw GatewayException("CARD_NOT_FOUND", "未找到可操作的银行卡", 404)
        val owned = state.cards[index]
        val status = when (pending.action) {
            "CARD_FREEZE" -> "FROZEN"
            "CARD_UNFREEZE", "CARD_UNREPORT_LOST" -> "ACTIVE"
            "CARD_REPORT_LOST" -> "LOST"
            else -> owned.view.status
        }
        val limit = if (pending.action == "CARD_LIMIT") pending.details.number("amount") else owned.view.dailyLimit
        val updated = owned.copy(view = owned.view.copy(status = status, dailyLimit = limit, dailyRemaining = (limit - owned.view.dailySpentToday).coerceAtLeast(0.0)))
        state.cards[index] = updated
        return completed(
            pending.conversationId, pending.intent, "银行卡操作已完成。",
            mapOf("execution_result" to mapOf("type" to pending.action, "data" to mapOf("status" to if (pending.action == "CARD_LIMIT") "LIMIT_UPDATED" else status, "card_id" to updated.view.cardId, "number_masked" to updated.view.numberMasked, "daily_limit" to limit))),
        )
    }

    private fun wealthPurchase(text: String, userId: String, conversation: String): TurnEnvelope {
        val product = productsFixture.firstOrNull { text.contains(it.productId) || text.contains(it.name) }
            ?: return completed(conversation, "WEALTH_PURCHASE", "请补充要申购的产品代码和金额，例如“申购 CJ01002 1000 元”。")
        val amount = extractAmount(text) ?: return completed(conversation, "WEALTH_PURCHASE", "请补充申购金额。")
        if (amount < product.minAmount) return completed(conversation, "WEALTH_PURCHASE", "${product.name} 起购金额为 ${money(product.minAmount)}。")
        if ((state.balances[userId] ?: 0.0) - (state.locked[userId] ?: 0.0) < amount) return completed(conversation, "WEALTH_PURCHASE", "可用余额不足，未创建申购确认。")
        return approval(userId, conversation, "WEALTH_PURCHASE", "WEALTH_PURCHASE", "申购 ${product.name} ${money(amount)}（红色风险）", "RED", mapOf("product_id" to product.productId, "amount" to amount))
    }

    private fun executePurchase(pending: PendingApproval): TurnEnvelope {
        val product = productsFixture.first { it.productId == pending.details["product_id"] }
        val amount = pending.details.number("amount")
        val balance = state.balances[pending.userId] ?: 0.0
        if (balance - (state.locked[pending.userId] ?: 0.0) < amount) throw GatewayException("INSUFFICIENT_FUNDS", "可用余额不足，申购未执行", 409)
        state.balances[pending.userId] = roundMoney(balance - amount)
        val holding = HoldingView("HOLD-${UUID.randomUUID().toString().take(8).uppercase()}", product.productId, product.name, amount, amount, now())
        state.holdings.add(OwnedHolding(pending.userId, holding))
        return completed(
            pending.conversationId, pending.intent, "申购申请已受理，金额 ${money(amount)}。演示产品净值为 Mock 数据。",
            mapOf("execution_result" to mapOf("type" to "WEALTH_PURCHASE", "data" to mapOf("status" to "PURCHASED", "amount" to amount, "product_id" to product.productId, "product_name" to product.name, "holding_id" to holding.holdingId))),
        )
    }

    private fun wealthRedeem(text: String, userId: String, conversation: String): TurnEnvelope {
        val holding = state.holdings.firstOrNull { it.ownerId == userId && (text.contains(it.view.holdingId) || text.contains(it.view.productName)) }
            ?: state.holdings.firstOrNull { it.ownerId == userId }
            ?: return completed(conversation, "WEALTH_REDEEM", "您当前没有可赎回的本地持仓。")
        val amount = extractAmount(text) ?: holding.view.principal
        return approval(userId, conversation, "WEALTH_REDEEM", "WEALTH_REDEEM", "赎回 ${holding.view.productName} ${money(amount)}（红色风险）", "RED", mapOf("holding_id" to holding.view.holdingId, "amount" to amount))
    }

    private fun executeRedeem(pending: PendingApproval): TurnEnvelope {
        val index = state.holdings.indexOfFirst { it.ownerId == pending.userId && it.view.holdingId == pending.details["holding_id"] }
        if (index < 0) throw GatewayException("HOLDING_NOT_FOUND", "持仓不存在", 404)
        val owned = state.holdings[index]
        val amount = pending.details.number("amount").coerceAtMost(owned.view.principal)
        val remaining = roundMoney(owned.view.principal - amount)
        if (remaining <= 0) state.holdings.removeAt(index) else state.holdings[index] = owned.copy(view = owned.view.copy(principal = remaining, estimatedValue = remaining))
        state.balances[pending.userId] = roundMoney((state.balances[pending.userId] ?: 0.0) + amount)
        return completed(
            pending.conversationId, pending.intent, "赎回申请已受理，金额 ${money(amount)}。",
            mapOf("execution_result" to mapOf("type" to "WEALTH_REDEEM", "data" to mapOf("status" to "REDEEMED", "redeemed_amount" to amount, "holding_id" to owned.view.holdingId))),
        )
    }

    private fun executeCrossScene(pending: PendingApproval): TurnEnvelope {
        val amount = pending.details.number("amount")
        val available = (state.balances[pending.userId] ?: 0.0) - (state.locked[pending.userId] ?: 0.0)
        if (available < amount) throw GatewayException("INSUFFICIENT_FUNDS", "可用余额不足，未锁定资金", 409)
        state.locked[pending.userId] = roundMoney((state.locked[pending.userId] ?: 0.0) + amount)
        val parent = "TASK-${UUID.randomUUID().toString().take(8).uppercase()}"
        val flower = "TASK-${UUID.randomUUID().toString().take(8).uppercase()}"
        val cake = "TASK-${UUID.randomUUID().toString().take(8).uppercase()}"
        state.tasks.addAll(listOf(
            OwnedTask(pending.userId, TaskView(parent, "BIRTHDAY_PLAN", "PENDING", LocalDate.now().plusMonths(1).minusDays(2).toString(), now(), now(), payload = mapOf("locked_amount" to amount))),
            OwnedTask(pending.userId, TaskView(flower, "FLOWER_DRAFT", "PENDING", LocalDate.now().plusMonths(1).minusDays(2).toString(), now(), now(), dependsOn = listOf(parent), payload = mapOf("item" to "鲜花"))),
            OwnedTask(pending.userId, TaskView(cake, "CAKE_DRAFT", "PENDING", LocalDate.now().plusMonths(1).minusDays(2).toString(), now(), now(), dependsOn = listOf(parent), payload = mapOf("item" to "蛋糕"))),
        ))
        return completed(
            pending.conversationId, pending.intent, "已锁定 ${money(amount)}，并建立生日前 2 天的鲜花与蛋糕订单草稿。到期前仍需您确认，不会自动消费。",
            mapOf(
                "execution_result" to mapOf("type" to "LOCK_SUCCESS", "data" to mapOf("status" to "LOCKED", "locked_amount" to amount, "remaining_available" to available - amount, "task_id" to parent)),
                "todo_card" to mapOf("title" to "爱人生日准备", "locked_fund" to money(amount), "schedule" to "生日前 2 天", "status" to "待确认", "task_id" to parent, "order_task_ids" to listOf(flower, cake)),
            ),
        )
    }

    private fun balanceQuery(userId: String, conversation: String): TurnEnvelope {
        val balance = state.balances[userId] ?: 0.0
        val locked = state.locked[userId] ?: 0.0
        return completed(
            conversation, "BALANCE_QUERY", "您的账户总余额为 ${money(balance)}，可用余额为 ${money(balance - locked)}。",
            mapOf("execution_result" to mapOf("type" to "BALANCE", "data" to mapOf("total_balance" to balance, "available_balance" to balance - locked, "locked_amount" to locked, "currency" to "CNY"))),
        )
    }

    private fun billQuery(conversation: String): TurnEnvelope = completed(
        conversation, "BILL_ANALYSIS", "本月共记录 8 笔支出，主要集中在餐饮、交通和订阅。检测到 1 笔境外消费需要您复核。",
        mapOf("execution_result" to mapOf("type" to "BILL_ANALYSIS", "facts" to mapOf(
            "record_count" to 8, "total_spent" to 1838.0, "refund_total" to 120.0, "net_spent" to 1718.0,
            "category_breakdown" to listOf(
                mapOf("category" to "餐饮", "amount" to 680.0, "share" to 0.396),
                mapOf("category" to "交通", "amount" to 335.0, "share" to 0.195),
                mapOf("category" to "订阅代扣", "amount" to 40.0, "share" to 0.023),
            ),
            "anomalies" to listOf(mapOf("tx_id" to "TX-OVERSEAS-001", "merchant" to "境外商户", "amount" to 688.0, "reasons" to listOf("异地", "夜间", "首次商户"))),
            "analysis_engine" to "应用内只读统计沙箱",
        ))),
    )

    private fun wealthQuery(conversation: String): TurnEnvelope = completed(
        conversation, "WEALTH_QUERY",
        "在售理财产品共 4 款。以下均为虚构演示产品，业绩比较基准不代表收益承诺，也不构成投资建议。\n" + productsFixture.joinToString("\n") { "- `${it.productId}` ${it.name} · 风险 ${it.riskLevel} · ${it.benchmark}" },
        mapOf("execution_result" to mapOf("type" to "WEALTH_QUERY", "data" to mapOf("code" to 200, "data" to productsFixture.map(::productMap), "disclaimer" to "Mock 产品，不构成投资建议"))),
    )

    private fun holdingsQuery(userId: String, conversation: String): TurnEnvelope {
        val list = state.holdings.filter { it.ownerId == userId }.map { it.view }
        val reply = if (list.isEmpty()) "您当前没有理财持仓。" else "您当前有 ${list.size} 笔理财持仓。"
        return completed(conversation, "WEALTH_HOLDINGS", reply, mapOf("execution_result" to mapOf("type" to "WEALTH_HOLDINGS", "data" to mapOf("count" to list.size, "data" to list.map { mapOf("holding_id" to it.holdingId, "product_id" to it.productId, "product_name" to it.productName, "principal" to it.principal, "estimated_value" to it.estimatedValue) }))))
    }

    private fun subscriptionQuery(userId: String, conversation: String): TurnEnvelope {
        val list = state.subscriptions.filter { it.ownerId == userId }
        val active = list.count { it.status == "ACTIVE" }
        val lines = list.joinToString("\n") { "- ${it.merchant}｜每期 ${money(it.amount)}｜下次扣款 ${it.nextCharge}｜状态 ${if (it.status == "ACTIVE") "生效中" else "已取消"}" }
        return completed(
            conversation, "SUBSCRIPTION_QUERY", "订阅/代扣共 ${list.size} 项，其中 $active 项生效中：\n$lines",
            mapOf("execution_result" to mapOf("type" to "SUBSCRIPTION_QUERY", "data" to mapOf("code" to 200, "data" to list.map { mapOf("subscription_id" to it.subscriptionId, "merchant" to it.merchant, "amount" to it.amount, "next_charge" to it.nextCharge, "status" to it.status) }))),
        )
    }

    private fun cardQuery(userId: String, conversation: String): TurnEnvelope {
        val cards = state.cards.filter { it.ownerId == userId }
        return completed(
            conversation, "CARD_QUERY", "您名下共有 ${cards.size} 张银行卡，可在“卡包”查看详情。",
            mapOf("execution_result" to mapOf("type" to "CARD_QUERY", "data" to mapOf("count" to cards.size, "status" to "SUCCESS"))),
        )
    }

    private fun completed(
        conversation: String,
        intent: String,
        reply: String,
        result: Map<String, Any>? = null,
    ) = TurnEnvelope(200, conversation, TurnEnvelope.STATUS_COMPLETED, reply, requestId(), intent, "GREEN", null, result)

    private fun verifyAttestation(userId: String, approvalId: String, raw: String?) {
        if (raw.isNullOrBlank() || !raw.startsWith(DeviceKeyManager.ENVELOPE_PREFIX)) {
            throw GatewayException("DEVICE_ATTESTATION_REQUIRED", "需要完成设备签名验证", 403)
        }
        val payload = runCatching {
            val json = String(Base64.getDecoder().decode(raw.removePrefix(DeviceKeyManager.ENVELOPE_PREFIX)), Charsets.UTF_8)
            JsonParser.parseString(json).asJsonObject
        }.getOrElse { throw GatewayException("DEVICE_ATTESTATION_INVALID", "设备签名格式无效", 403) }
        val deviceId = payload.get("device_id")?.asString.orEmpty()
        val challengeId = payload.get("challenge_id")?.asString.orEmpty()
        val signature = payload.get("signature")?.asString.orEmpty()
        val challenge = challenges.remove(challengeId)
            ?: throw GatewayException("CHALLENGE_INVALID", "设备验证请求不存在或已使用", 403)
        if (challenge.approvalId != approvalId || challenge.userId != userId || challenge.expiresAt < epochSeconds()) {
            throw GatewayException("CHALLENGE_INVALID", "设备验证请求已过期或与当前操作不匹配", 403)
        }
        val device = ownedDevice(deviceId, userId)
        if (device.status != "ACTIVE") throw GatewayException("DEVICE_REVOKED", "当前设备已被撤销", 403)
        val valid = runCatching {
            val publicKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(Base64.getDecoder().decode(device.publicKey)))
            Signature.getInstance("SHA256withECDSA").run {
                initVerify(publicKey)
                update(DeviceKeyManager.canonicalPayload(challengeId, userId, challenge.authTokenJti).toByteArray(Charsets.UTF_8))
                verify(Base64.getDecoder().decode(signature))
            }
        }.getOrDefault(false)
        if (!valid) throw GatewayException("DEVICE_SIGNATURE_INVALID", "设备签名验证失败", 403)
    }

    private fun authenticate(userId: String, sessionToken: String?): String {
        val subject = session(sessionToken)
        if (subject != userId) throw GatewayException("FORBIDDEN", "登录身份与请求账户不一致", 403)
        val lockedUntil = state.lockedUntil[userId] ?: 0L
        if (lockedUntil > epochSeconds()) {
            throw GatewayException("SECURITY_LOCKED", "检测到连续验证失败，账户操作已临时锁定，请稍后再试", 423)
        }
        return subject
    }

    private fun session(token: String?): String {
        val session = token?.let(state.sessions::get)
            ?: throw GatewayException("AUTHENTICATION_REQUIRED", "登录状态已失效，请重新登录", 401)
        if (session.expiresAt < epochSeconds()) {
            state.sessions.remove(token)
            save()
            throw GatewayException("AUTHENTICATION_REQUIRED", "登录状态已过期，请重新登录", 401)
        }
        return session.userId
    }

    private fun requireOwner(pathUser: String, headerUser: String) {
        if (pathUser != headerUser) throw GatewayException("FORBIDDEN", "不能访问其他账户的数据", 403)
    }

    private fun ownedDevice(deviceId: String, userId: String): StoredDevice =
        state.devices.firstOrNull { it.deviceId == deviceId && it.userId == userId }
            ?: throw GatewayException("DEVICE_NOT_FOUND", "设备不存在", 404)

    private fun audit(userId: String, action: String, outcome: String) {
        val previous = state.audit.lastOrNull()?.hash.orEmpty()
        val at = now()
        val hash = sha256("$previous|$at|$userId|$action|$outcome")
        state.audit.add(AuditEntry(at, userId, action, outcome, previous, hash))
        if (state.audit.size > 500) state.audit = state.audit.takeLast(500).toMutableList()
    }

    private fun recordSecurityFailure(userId: String) {
        val cutoff = epochSeconds() - 300
        val failures = state.securityFailures.getOrPut(userId) { mutableListOf() }
        failures.removeAll { it < cutoff }
        failures.add(epochSeconds())
        if (failures.size >= 3) {
            state.lockedUntil[userId] = epochSeconds() + 300
            audit(userId, "SECURITY_CIRCUIT_BREAKER", "LOCKED")
        }
        save()
    }

    private fun save() {
        preferences?.edit()?.putString(SNAPSHOT, gson.toJson(state))?.apply()
    }

    private fun normalizeState() {
        if (state.balances.isEmpty()) state.balances = defaultBalances()
        if (state.cards.isEmpty()) state.cards = defaultCards()
        if (state.subscriptions.isEmpty()) state.subscriptions = defaultSubscriptions()
    }

    private fun looksLikeInjection(text: String) = listOf(
        "忽略之前", "无视之前", "systemprompt", "系统提示词", "绕过权限", "跳过授权", "伪造成功", "developer模式", "jailbreak",
    ).any(text::contains)

    private fun isTransfer(text: String) =
        (text.contains("转") && !text.contains("转账记录")) || text.contains("汇款") || text.contains("打钱") || text.contains("再来一次")

    private fun extractAmount(text: String): Double? = Regex("""(?:¥|￥)?\s*(\d{1,9}(?:\.\d{1,2})?)\s*(?:元|块)?""")
        .findAll(text).mapNotNull { it.groupValues[1].toDoubleOrNull() }.lastOrNull()

    private fun extractRecipient(text: String): String? {
        Regex("""(?:给|向)\s*([\p{L}\d]{2,20}?)(?:转账|转|汇款|打钱)""").find(text)?.groupValues?.getOrNull(1)?.let { return it }
        Regex("""(1\d{10})""").find(text)?.value?.let { return it }
        return null
    }

    private fun cardActionSummary(action: String, text: String): String = when (action) {
        "CARD_FREEZE" -> "冻结银行卡（红色风险）"
        "CARD_UNFREEZE" -> "解除银行卡冻结（红色风险）"
        "CARD_REPORT_LOST" -> "挂失银行卡（红色风险）"
        "CARD_UNREPORT_LOST" -> "解除银行卡挂失（红色风险）"
        else -> "将银行卡每日限额调整为 ${money(extractAmount(text) ?: 5000.0)}（红色风险）"
    }

    private fun relationLabel(code: String) = when (code) {
        "SPOUSE" -> "配偶"; "FAMILY" -> "家人"; "FRIEND" -> "朋友"; "COLLEAGUE" -> "同事"; else -> "其他"
    }

    private fun luhn(number: String): Boolean {
        var sum = 0
        var alternate = false
        for (char in number.reversed()) {
            var digit = char.digitToInt()
            if (alternate) { digit *= 2; if (digit > 9) digit -= 9 }
            sum += digit
            alternate = !alternate
        }
        return sum % 10 == 0
    }

    private fun productMap(product: ProductView): Map<String, Any> = mapOf(
        "product_id" to product.productId, "name" to product.name, "category" to (product.category ?: "理财产品"),
        "risk_level" to product.riskLevel, "benchmark" to (product.benchmark ?: "—"), "min_amount" to product.minAmount,
        "term" to (product.term ?: "—"), "liquidity" to (product.liquidity ?: "—"), "underlying" to (product.underlying ?: "—"),
    )

    private fun riskQuestions(): List<Map<String, Any>> = listOf(
        "您的年龄范围是？", "可投资资金占家庭资产比例？", "投资经验有多久？", "主要投资目标？",
        "可接受的最大年度亏损？", "预计持有期限？", "收入稳定程度？", "遇到短期下跌会如何处理？",
    ).mapIndexed { index, question ->
        mapOf("number" to index + 1, "text" to question, "options" to listOf(
            mapOf("key" to "A", "label" to "偏保守"), mapOf("key" to "B", "label" to "适中"), mapOf("key" to "C", "label" to "可承受波动"), mapOf("key" to "D", "label" to "积极进取"),
        ))
    }

    private fun requestId() = "local_${UUID.randomUUID()}"
    private fun now() = LocalDateTime.now().format(DateTimeFormatter.ISO_LOCAL_DATE_TIME)
    private fun epochSeconds() = System.currentTimeMillis() / 1000
    private fun money(value: Double) = "¥%,.2f".format(value)
    private fun roundMoney(value: Double) = round(value * 100) / 100
    private fun sha256(value: String) = MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }
    private fun Map<String, Any>.number(key: String) = (this[key] as? Number)?.toDouble() ?: 0.0

    private fun StoredDevice.view() = DeviceView(200, deviceId, userId, fingerprint, "EC_P256_SHA256", status, label, createdAt, lastSeenAt, pushEnabled)

    private data class PendingApproval(
        val id: String, val userId: String, val conversationId: String, val intent: String,
        val action: String, val risk: String, val details: Map<String, Any>, val expiresAt: Long,
    )
    private data class LocalChallenge(val id: String, val approvalId: String, val userId: String, val authTokenJti: String, val expiresAt: Long)
    private data class LocalSession(val userId: String, val expiresAt: Long)
    private data class LastTransfer(val recipient: String, val amount: Double)
    private data class StoredDevice(
        val deviceId: String, val userId: String, var publicKey: String, val fingerprint: String,
        var status: String, val label: String?, val createdAt: Long, var lastSeenAt: Long, var pushEnabled: Boolean = false,
    )
    private data class OwnedCard(val ownerId: String, val view: CardView, val fullNumber: String)
    private data class OwnedContact(val ownerId: String, val view: ContactView)
    private data class OwnedHolding(val ownerId: String, val view: HoldingView)
    private data class OwnedTask(val ownerId: String, val view: TaskView)
    private data class LocalSubscription(
        val ownerId: String, val subscriptionId: String, val merchant: String, val amount: Double,
        val nextCharge: String, var status: String,
    )
    private data class LocalTransaction(
        val transactionId: String, val ownerId: String, val merchant: String, val category: String,
        val amount: Double, val time: String,
    )
    private data class AuditEntry(
        val at: String, val userId: String, val action: String, val outcome: String,
        val previousHash: String, val hash: String,
    )
    private data class LocalState(
        val schema: Int = STATE_SCHEMA,
        var balances: MutableMap<String, Double> = mutableMapOf(),
        var locked: MutableMap<String, Double> = mutableMapOf(),
        var sessions: MutableMap<String, LocalSession> = mutableMapOf(),
        var devices: MutableList<StoredDevice> = mutableListOf(),
        var cards: MutableList<OwnedCard> = mutableListOf(),
        var contacts: MutableList<OwnedContact> = mutableListOf(),
        var holdings: MutableList<OwnedHolding> = mutableListOf(),
        var tasks: MutableList<OwnedTask> = mutableListOf(),
        var subscriptions: MutableList<LocalSubscription> = mutableListOf(),
        var transactions: MutableList<LocalTransaction> = mutableListOf(),
        var riskProfiles: MutableMap<String, String> = mutableMapOf(),
        var lastTransfers: MutableMap<String, LastTransfer> = mutableMapOf(),
        var securityFailures: MutableMap<String, MutableList<Long>> = mutableMapOf(),
        var lockedUntil: MutableMap<String, Long> = mutableMapOf(),
        var audit: MutableList<AuditEntry> = mutableListOf(),
    )

    private fun defaultState() = LocalState(
        balances = defaultBalances(),
        locked = mutableMapOf("user_zhang" to 0.0, "user_li" to 0.0, "user_wife" to 0.0),
        cards = defaultCards(),
        contacts = mutableListOf(
            OwnedContact("user_zhang", ContactView("CONTACT-001", "李四", "13800138001", "FRIEND", "朋友", accountId = "user_li", linkedExisting = true, accountName = "李四", createdAt = "2026-01-01T09:00:00")),
            OwnedContact("user_zhang", ContactView("CONTACT-002", "爱人", "13800138002", "SPOUSE", "配偶", "1990-10-05", "user_wife", true, "王女士", "2026-01-01T09:00:00")),
        ),
        subscriptions = defaultSubscriptions(),
        transactions = mutableListOf(
            LocalTransaction("TX1001", "user_zhang", "社区超市", "日用", 125.0, "2026-09-03T18:30:00"),
            LocalTransaction("TX1002", "user_zhang", "Apple 订阅", "订阅代扣", 15.0, "2026-09-03T08:00:00"),
            LocalTransaction("TX1003", "user_zhang", "境外商户", "其他", 688.0, "2026-09-21T23:48:00"),
        ),
        riskProfiles = mutableMapOf(),
    )

    private fun defaultBalances() = mutableMapOf("user_zhang" to 15_000.0, "user_li" to 8_000.0, "user_wife" to 12_000.0)

    private fun defaultCards() = mutableListOf(
        OwnedCard("user_zhang", CardView("CARD-001", "6222 **** **** 3082", "3082", "借记卡", "ACTIVE", 5000.0, 200.0, 4800.0, 15000.0, "工资卡"), "6222020000023082"),
        OwnedCard("user_zhang", CardView("CARD-002", "6225 **** **** 7788", "7788", "借记卡", "LOST", 3000.0, 0.0, 3000.0, 15000.0, "生活卡"), "6225888888887788"),
    )

    private fun defaultSubscriptions() = mutableListOf(
        LocalSubscription("user_zhang", "SUB-APPLE", "Apple 订阅", 15.0, "2026-10-03", "ACTIVE"),
        LocalSubscription("user_zhang", "SUB-VIDEO", "视频会员", 25.0, "2026-09-15", "ACTIVE"),
    )

    private val productsFixture = listOf(
        product("CJ01002", "稳健添利·日日盈", "现金管理类", "R1", "2.05% ~ 2.35%（7日年化）", 1.0, 2.61, "灵活申赎", 1.0261),
        product("AY90204", "安逸安享·季季丰90天", "固定收益类", "R2", "2.75% ~ 3.10%（年化）", 1000.0, 3.05, "90 天封闭", 1.0738),
        product("ZY18031", "智远创赢·成长平衡180天", "固收+混合类", "R3", "3.60% ~ 4.80%（年化）", 10000.0, 19.46, "180天运作期", 1.1946),
        product("QK36509", "乾坤进取·产业智选封闭1年", "权益类", "R4", "沪深300×70% + 中债综合×30%", 50000.0, 12.18, "365 天封闭", 1.1218),
    )

    private fun product(
        id: String, name: String, category: String, risk: String, benchmark: String,
        minimum: Double, annual: Double, term: String, nav: Double,
    ) = ProductView(
        productId = id, name = name, category = category, riskLevel = risk, benchmark = benchmark,
        annualizedYield = annual / 100, minAmount = minimum, liquidity = term, term = term,
        underlying = "虚构演示资产组合", targetClients = "与风险承受能力匹配的演示用户",
        dataSource = "MOCK_FIXTURE（非实时）", asOfDate = "2026-09-15", latestNav = nav,
        annualReturnPct = annual, recentMonthReturnPct = 0.68, netValueDate = "2026-09-15", dailyChangePct = 0.12,
        performanceHistory = listOf(
            PerformanceView("近1月", 0.68, 0.52, 0.48, "12/80"), PerformanceView("近3月", 3.81, 2.95, 2.76, "9/80"),
            PerformanceView("近6月", 8.72, 6.80, 6.21, "7/80"), PerformanceView("近1年", annual, annual * 0.82, annual * 0.76, "6/80"),
            PerformanceView("成立以来", annual + 4.0, annual, annual * 0.9, "5/80"),
        ),
        navSeries = listOf(
            NavPointView("2025-09-15", 1.0, 1.0), NavPointView("2025-12-15", 1.041, 1.032),
            NavPointView("2026-03-15", 1.083, 1.061), NavPointView("2026-06-15", 1.132, 1.092), NavPointView("2026-09-15", nav, 1.126),
        ),
        analysisMetrics = listOf(
            AnalysisMetricView("收益能力", 4, "基于 Mock 历史快照", "良好"), AnalysisMetricView("回撤控制", 3, "演示指标", "适中"),
        ),
        manager = ManagerView("林晨", "2021-03-04", 9.0, "虚构基金经理，仅用于界面演示", "投资经理", 45.8, annual, annual + 4.0),
        assetAllocation = listOf(AllocationView("固定收益", 70.0), AllocationView("权益资产", 25.0), AllocationView("现金", 5.0)),
        establishedAt = "2021-03-04", scaleBillion = 45.8, managerCompany = "安言演示理财有限公司", custodian = "演示银行",
        fundType = category, dividendMode = "红利再投资", originalFeeRate = 1.0, discountFeeRate = 0.1,
        ratingAgency = "演示评级 ★★★★", awardTitle = "Mock 产品展示", purchaseRule = "${minimum.toInt()}元起购，工作日 15:00 前申请",
        redemptionRule = "到期或开放日可赎回", confirmTime = "T+1", feeRate = 0.1,
        summary = "本产品及全部行情为虚构演示数据。", announcement = "Mock 行情快照，不构成投资建议。",
        promoTitle = "演示专享", promoSubtitle = "费率与收益仅作界面展示", institutionFoundedYears = 5,
        institutionAumBillion = 120.0, institutionRating = "演示评级", topHoldings = listOf(
            TopHoldingView("演示国债 A", "债券", 18.0, "MOCK001", 0.01), TopHoldingView("演示资产 B", "权益", 9.0, "MOCK002", 0.12),
        ),
    )
}
