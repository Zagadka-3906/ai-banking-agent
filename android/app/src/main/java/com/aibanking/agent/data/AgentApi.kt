package com.aibanking.agent.data

import com.google.gson.Gson
import com.google.gson.annotations.SerializedName
import okhttp3.ResponseBody
import retrofit2.HttpException
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.Header
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Streaming

/**
 * DTOs mirroring the channel gateway's public contract.
 *
 * Note what is *absent*: there is no field carrying an authorization nonce or an
 * MFA proof.  The gateway keeps both server-side and exposes only an opaque,
 * single-use approval handle, so a client cannot leak or replay the credential
 * that actually authorizes a ledger mutation.
 */

data class MessageRequest(
    val text: String,
    @SerializedName("conversation_id") val conversationId: String? = null,
    @SerializedName("message_id") val messageId: String? = null,
)

data class ApprovalRequest(
    val decision: String,
    val otp: String? = null,
    @SerializedName("face_attestation") val faceAttestation: String? = null,
)

data class ApprovalView(
    @SerializedName("approval_id") val approvalId: String,
    @SerializedName("risk_level") val riskLevel: String = "YELLOW",
    @SerializedName("required_actions") val requiredActions: List<String> = emptyList(),
    val summary: String = "",
    @SerializedName("mfa_required") val mfaRequired: Boolean = false,
    @SerializedName("expires_in") val expiresIn: Int = 0,
)

data class TurnEnvelope(
    val code: Int = 0,
    @SerializedName("conversation_id") val conversationId: String = "",
    val status: String = "",
    val reply: String = "",
    @SerializedName("request_id") val requestId: String? = null,
    val intent: String? = null,
    @SerializedName("risk_level") val riskLevel: String? = null,
    val approval: ApprovalView? = null,
    val result: Map<String, Any>? = null,
    @SerializedName("business_status") val businessStatus: String? = null,
    val response: ReplyResponse? = null,
) {
    val approvalRequired: Boolean get() = status == STATUS_APPROVAL_REQUIRED
    val displayResult: Map<String, Any>? get() = if (response?.version == 1 && response.sections.orEmpty().isNotEmpty()) {
        (result.orEmpty() - "execution_result") + ("execution_results" to response.sections.orEmpty().map { it.data })
    } else result

    companion object {
        const val STATUS_COMPLETED = "COMPLETED"
        const val STATUS_APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    }
}

data class ReplySection(val type: String? = null, val status: String = "", val data: Map<String, Any> = emptyMap())
data class WorkflowStep(val index: Int = 0, val label: String = "", val status: String = "PENDING")
data class WorkflowPlan(val id: String = "", @SerializedName("current_step") val currentStep: Int = 1, val steps: List<WorkflowStep> = emptyList())
data class ReplyResponse(val version: Int = 0, val status: String = "", val summary: String = "",
    val sections: List<ReplySection> = emptyList(),
    @SerializedName("workflow_plan") val workflowPlan: WorkflowPlan? = null,
    @SerializedName("operation_id") val operationId: String? = null)
data class StageProgress(val stage: String = "", val label: String = "", val phase: String = "started",
    @SerializedName("elapsed_ms") val elapsedMs: Long = 0, val step: Int = 1)

data class DemoOtpView(
    @SerializedName("approval_id") val approvalId: String = "",
    @SerializedName("demo_code") val demoCode: String? = null,
    @SerializedName("expires_in") val expiresIn: Int = 0,
)

data class FaceChallengeView(
    val code: Int = 0,
    @SerializedName("approval_id") val approvalId: String = "",
    @SerializedName("challenge_id") val challengeId: String = "",
    @SerializedName("auth_token_jti") val authTokenJti: String = "",
    @SerializedName("expires_in") val expiresIn: Int? = null,
)

data class FeishuBindingCodeView(
    val code: Int = 0,
    @SerializedName("binding_code") val bindingCode: String = "",
    @SerializedName("expires_in") val expiresIn: Int = 0,
)

data class FeishuUnbindView(val code: Int = 0, val status: String = "")

/** A normalized gateway/transport failure, so the UI never shows a raw stack. */
class GatewayException(
    val code: String?,
    override val message: String,
    val httpStatus: Int,
) : Exception(message) {
    val sessionExpired: Boolean get() = httpStatus == 401 || code == "AUTHENTICATION_REQUIRED"
}

/**
 * Normalize an HTTP failure into a [GatewayException].
 *
 * Shared by every repository so the core's ``{"detail":{"code","message"}}``
 * envelope is parsed in exactly one place; two copies would drift and one
 * screen would start showing "HTTP 401" while another showed the real reason.
 */
fun HttpException.asGatewayException(gson: Gson = Gson()): GatewayException {
    val raw = runCatching { response()?.errorBody()?.string() }.getOrNull()
    val detail = runCatching { gson.fromJson(raw, ErrorEnvelope::class.java)?.detail }.getOrNull()
    return GatewayException(
        code = detail?.code,
        message = detail?.message ?: "服务返回 HTTP ${code()}",
        httpStatus = code(),
    )
}

private data class ErrorEnvelope(val detail: ErrorDetail?)

private data class ErrorDetail(val code: String?, val message: String?)

/** One decoded frame of the turn stream. */
sealed interface StreamEvent {
    /** A graph node is running; [label] is user-facing progress text. */
    data class Stage(val stage: String, val label: String) : StreamEvent

    /** Terminal: the turn finished. */
    data class Completed(val envelope: TurnEnvelope) : StreamEvent

    /** Terminal: the turn is waiting for a human decision. */
    data class ApprovalRequired(val envelope: TurnEnvelope) : StreamEvent

    /** Terminal: the server reported a failure in-band. */
    data class Failed(val code: String?, val message: String) : StreamEvent
}

interface AgentApi {
    @POST("api/v1/agent/approvals/{approvalId}/demo-otp")
    suspend fun demoOtp(@Path("approvalId") approvalId: String,
        @Header("X-User-Id") userId: String, @Header("X-Session-Token") sessionToken: String?): DemoOtpView
    @Streaming
    @POST("api/v1/agent/approvals/{approvalId}/stream")
    suspend fun resolveApprovalStream(@Path("approvalId") approvalId: String, @Body body: ApprovalRequest,
        @Header("X-User-Id") userId: String, @Header("X-Session-Token") sessionToken: String?): ResponseBody

    @GET("api/v1/agent/conversations/{conversationId}/operations/{operationId}")
    suspend fun reconcileOperation(@Path("conversationId") conversationId: String, @Path("operationId") operationId: String,
        @Header("X-User-Id") userId: String, @Header("X-Session-Token") sessionToken: String?): TurnEnvelope

    @POST("api/v1/im/feishu/binding-code")
    suspend fun issueFeishuBindingCode(
        @Header("X-Session-Token") sessionToken: String,
    ): FeishuBindingCodeView

    @DELETE("api/v1/im/feishu/binding")
    suspend fun unbindFeishu(
        @Header("X-Session-Token") sessionToken: String,
    ): FeishuUnbindView

    @POST("api/v1/agent/messages")
    suspend fun sendMessage(
        @Body body: MessageRequest,
        @Header("X-User-Id") userId: String,
        @Header("X-Session-Token") sessionToken: String?,
    ): TurnEnvelope

    /**
     * The same turn, streamed as Server-Sent Events.
     *
     * `@Streaming` is essential: without it Retrofit buffers the whole body
     * before returning, which would turn a progress stream back into a
     * single blocking call.
     */
    @Streaming
    @POST("api/v1/agent/messages/stream")
    suspend fun sendMessageStream(
        @Body body: MessageRequest,
        @Header("X-User-Id") userId: String,
        @Header("X-Session-Token") sessionToken: String?,
    ): ResponseBody

    @POST("api/v1/agent/approvals/{approvalId}")
    suspend fun resolveApproval(
        @Path("approvalId") approvalId: String,
        @Body body: ApprovalRequest,
        @Header("X-User-Id") userId: String,
        @Header("X-Session-Token") sessionToken: String?,
    ): TurnEnvelope

    @POST("api/v1/agent/approvals/{approvalId}/face-challenge")
    suspend fun faceChallenge(
        @Path("approvalId") approvalId: String,
        @Header("X-User-Id") userId: String,
        @Header("X-Session-Token") sessionToken: String?,
    ): FaceChallengeView
}
