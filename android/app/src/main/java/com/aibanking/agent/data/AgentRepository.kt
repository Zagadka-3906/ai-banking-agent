package com.aibanking.agent.data

import com.aibanking.agent.BuildConfig
import com.google.gson.Gson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.ResponseBody
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.HttpException
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * The only path from the app to the banking agent.
 *
 * Transport concerns live here: timeouts, error normalization and correlation
 * ids.  It deliberately never retries a money-moving request - a timeout must
 * not turn into a duplicate financial action; the gateway's idempotency key
 * (the client-generated message id) is what makes an explicit user retry safe.
 */
class AgentRepository(
    private val api: AgentApi,
    private val gson: Gson = Gson(),
) {

    suspend fun issueFeishuBindingCode(sessionToken: String): FeishuBindingCodeView = normalizing {
        api.issueFeishuBindingCode(sessionToken)
    }

    suspend fun unbindFeishu(sessionToken: String): FeishuUnbindView = normalizing {
        api.unbindFeishu(sessionToken)
    }

    suspend fun sendMessage(
        principal: String,
        sessionToken: String?,
        conversationId: String?,
        text: String,
        messageId: String = UUID.randomUUID().toString(),
    ): TurnEnvelope = normalizing {
        api.sendMessage(
            MessageRequest(text = text, conversationId = conversationId, messageId = messageId),
            userId = principal,
            sessionToken = sessionToken,
        )
    }

    suspend fun resolveApproval(
        principal: String,
        sessionToken: String?,
        approvalId: String,
        decision: String,
        otp: String? = null,
        faceAttestation: String? = null,
        onProgress: suspend (StageProgress) -> Unit = {},
    ): TurnEnvelope = withContext(Dispatchers.IO) { normalizing {
        readStream(api.resolveApprovalStream(
            approvalId = approvalId,
            body = ApprovalRequest(decision = decision, otp = otp, faceAttestation = faceAttestation),
            userId = principal,
            sessionToken = sessionToken,
        ), {}, onProgress)
    } }

    suspend fun reconcileOperation(principal: String, sessionToken: String?, conversationId: String, operationId: String): TurnEnvelope = normalizing {
        api.reconcileOperation(conversationId, operationId, principal, sessionToken)
    }

    suspend fun requestFaceChallenge(
        principal: String,
        sessionToken: String?,
        approvalId: String,
    ): FaceChallengeView = normalizing {
        api.faceChallenge(approvalId = approvalId, userId = principal, sessionToken = sessionToken)
    }

    /**
     * Run a turn and report its progress as it happens.
     *
     * Returns the terminal envelope (completed or awaiting approval) and invokes
     * [onStage] with the user-facing label of each graph node as it runs.  An
     * in-band `error` frame is raised as a [GatewayException] so callers have one
     * failure path whether the problem arrived as a status code or as an event.
     */
    suspend fun streamMessage(
        principal: String,
        sessionToken: String?,
        conversationId: String?,
        text: String,
        messageId: String = UUID.randomUUID().toString(),
        onProgress: suspend (StageProgress) -> Unit = {},
        onStage: suspend (String) -> Unit = {},
    ): TurnEnvelope = withContext(Dispatchers.IO) {
        // Reading an event stream is blocking socket IO.  The caller is a
        // ViewModel coroutine on Dispatchers.Main, so without this the reads -
        // and the whole turn - would happen on the UI thread, which Android
        // rejects outright (NetworkOnMainThreadException) and which would freeze
        // the app even where it did not.
        normalizing {
            val body = api.sendMessageStream(
                MessageRequest(text = text, conversationId = conversationId, messageId = messageId),
                userId = principal,
                sessionToken = sessionToken,
            )
            readStream(body, onStage, onProgress)
        }
    }

    private suspend fun readStream(body: ResponseBody, onStage: suspend (String) -> Unit, onProgress: suspend (StageProgress) -> Unit): TurnEnvelope {
            var terminal: TurnEnvelope? = null
            try {
                val reader = body.charStream().buffered()
                var event: String? = null
                val data = StringBuilder()
                while (true) {
                    val line = reader.readLine() ?: break
                    when {
                        line.startsWith(EVENT_PREFIX) -> event = line.removePrefix(EVENT_PREFIX).trim()
                        line.startsWith(DATA_PREFIX) -> data.append(line.removePrefix(DATA_PREFIX).trim())
                        line.isBlank() -> {
                            val name = event ?: continue
                            terminal = handleFrame(name, data.toString(), onStage, onProgress) ?: terminal
                            if (terminal != null) break
                            event = null
                            data.setLength(0)
                        }
                    }
                }
            } finally {
                // Draining a finished event stream races with the server closing
                // the connection, and `use {}` would rethrow that close failure -
                // throwing away a result we already decoded successfully.
                runCatching { body.close() }
            }
            return terminal ?: throw GatewayException(null, "服务端未返回结果，请重试", 502)
    }

    /** Decode one SSE frame; returns an envelope only for terminal frames. */
    private suspend fun handleFrame(name: String, data: String, onStage: suspend (String) -> Unit, onProgress: suspend (StageProgress) -> Unit): TurnEnvelope? =
        when (name) {
            "stage" -> {
                val stage = gson.fromJson(data, StageProgress::class.java)
                onProgress(stage)
                if (stage.phase == "started" && stage.label.isNotBlank()) onStage("正在${stage.label}…")
                null
            }
            "result" -> gson.fromJson(data, TurnEnvelope::class.java)
            "approval" -> gson.fromJson(data, TurnEnvelope::class.java)
            "error" -> {
                // The in-band error frame is flat ({"code","message"}), unlike the
                // nested {"detail":{...}} envelope an HTTP error carries, so it is
                // decoded with its own type rather than reusing that one.
                val failure = gson.fromJson(data, ErrorFrame::class.java)
                throw GatewayException(failure?.code, failure?.message ?: "处理失败，请稍后重试", failure?.httpStatus ?: 0)
            }
            // An unrecognised frame is ignored rather than fatal: a newer server
            // may add event kinds this build does not know yet.
            else -> null
        }

    private data class ErrorFrame(val code: String? = null, val message: String? = null,
        @com.google.gson.annotations.SerializedName("http_status") val httpStatus: Int? = null)

    private suspend fun <T> normalizing(block: suspend () -> T): T = try {
        block()
    } catch (failure: HttpException) {
        throw failure.asGatewayException(gson)
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接渠道网关：${failure.message ?: "网络错误"}", 0)
    }

    companion object {
        private const val EVENT_PREFIX = "event:"
        private const val DATA_PREFIX = "data:"

        fun create(baseUrl: String = BuildConfig.GATEWAY_BASE_URL): AgentRepository {
            val client = OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                // A turn runs an LLM call plus a core precheck, so it is slower
                // than a plain REST call; the read timeout stays well under the
                // approval handle's 110 s lifetime so the user is never left
                // approving an already-expired nonce.
                .readTimeout(90, TimeUnit.SECONDS)
                .addInterceptor(
                    HttpLoggingInterceptor().apply {
                        // BASIC, never BODY: request/response bodies contain the
                        // user's banking instructions and their balances, which
                        // must not land in logcat.
                        level = if (BuildConfig.DEBUG) HttpLoggingInterceptor.Level.BASIC else HttpLoggingInterceptor.Level.NONE
                    },
                )
                .build()
            val retrofit = Retrofit.Builder()
                .baseUrl(baseUrl)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
            return AgentRepository(retrofit.create(AgentApi::class.java))
        }
    }
}
