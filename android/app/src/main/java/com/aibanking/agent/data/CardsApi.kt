package com.aibanking.agent.data

import com.aibanking.agent.BuildConfig
import com.google.gson.annotations.SerializedName
import okhttp3.OkHttpClient
import retrofit2.HttpException
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.POST
import retrofit2.http.Path
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * 银行卡管理（一户多卡，借记卡语义：卡余额与所属账户共享）。
 *
 * 卡号只回传脱敏形式；添加卡片为绿区操作（会话身份 + 审计），而冻结/
 * 挂失/调额属于红色风险操作——页面只把意图预填进聊天，由核心的
 * 短信 + 设备签名审批流最终授权。
 */

data class CardView(
    @SerializedName("card_id") val cardId: String = "",
    @SerializedName("number_masked") val numberMasked: String = "",
    @SerializedName("last4") val last4: String = "",
    val type: String = "",
    val status: String = "",
    @SerializedName("daily_limit") val dailyLimit: Double = 0.0,
    @SerializedName("daily_spent_today") val dailySpentToday: Double = 0.0,
    @SerializedName("daily_remaining") val dailyRemaining: Double? = null,
    @SerializedName("available_balance") val availableBalance: Double = 0.0,
    val label: String? = null,
)

data class CardListResponse(val code: Int = 0, val data: List<CardView> = emptyList())

data class CardAddRequest(
    val card_type: String = "借记卡",
    val label: String? = null,
    val card_number: String,
    val source: String = "MANUAL",
    val user_confirmed: Boolean = true,
)

data class CardAddResponse(val code: Int = 0, val status: String = "", val data: CardView? = null)

data class CardRevealRequest(val pin: String)
data class CardRevealView(
    @SerializedName("owner_name") val ownerName: String = "",
    @SerializedName("card_number") val cardNumber: String = "",
    @SerializedName("issuer_name") val issuerName: String = "",
    @SerializedName("expires_at") val expiresAt: String = "",
    @SerializedName("disclosure_id") val disclosureId: String = "",
)
data class CardRevealResponse(val code: Int = 0, val status: String = "", val data: CardRevealView? = null)

interface CardsApi {

    @GET("api/v1/cards/{userId}")
    suspend fun list(
        @Path("userId") userId: String,
        @Header("X-User-Id") userIdHeader: String,
        @Header("X-Session-Token") sessionToken: String,
    ): CardListResponse

    @POST("api/v1/cards/{userId}/add")
    suspend fun add(
        @Path("userId") userId: String,
        @Body body: CardAddRequest,
        @Header("X-User-Id") userIdHeader: String,
        @Header("X-Session-Token") sessionToken: String,
        // 核心按 (用户, 端点, 键) 缓存响应：同一个键重发只会回放上一次的结果，
        // 超时重试不会开出第二张卡。
        @Header("Idempotency-Key") idempotencyKey: String,
    ): CardAddResponse

    @POST("api/v1/cards/{userId}/{cardId}/reveal")
    suspend fun reveal(
        @Path("userId") userId: String,
        @Path("cardId") cardId: String,
        @Body body: CardRevealRequest,
        @Header("X-User-Id") userIdHeader: String,
        @Header("X-Session-Token") sessionToken: String,
    ): CardRevealResponse
}

class CardsRepository(private val api: CardsApi) {

    suspend fun list(principal: String, sessionToken: String): List<CardView> = try {
        api.list(userId = principal, userIdHeader = principal, sessionToken = sessionToken).data
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    /**
     * Issue one card.
     *
     * [idempotencyKey] is required rather than defaulted on purpose: generating a
     * fresh key per call would turn a read timeout plus an explicit retry into a
     * second card.  The caller owns one key per user submission and reuses it for
     * every retry of that submission.
     */
    suspend fun add(
        principal: String,
        sessionToken: String,
        cardType: String,
        label: String?,
        idempotencyKey: String,
        cardNumber: String,
        source: String,
    ): CardView = try {
        api.add(
            userId = principal,
            body = CardAddRequest(card_type = cardType, label = label?.takeIf { it.isNotBlank() }, card_number = cardNumber, source = source),
            userIdHeader = principal,
            sessionToken = sessionToken,
            idempotencyKey = idempotencyKey,
        ).data ?: throw GatewayException(null, "核心服务未返回卡片", 502)
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    suspend fun reveal(principal: String, sessionToken: String, cardId: String, pin: String): CardRevealView = try {
        api.reveal(principal, cardId, CardRevealRequest(pin), principal, sessionToken).data
            ?: throw GatewayException(null, "核心服务未返回卡片信息", 502)
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    companion object {
        fun create(baseUrl: String = BuildConfig.CORE_BASE_URL): CardsRepository {
            if (BuildConfig.STANDALONE_MODE) {
                return CardsRepository(com.aibanking.agent.data.standalone.StandaloneBank.cardsApi)
            }
            val client = OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(20, TimeUnit.SECONDS)
                .build()
            val retrofit = Retrofit.Builder()
                .baseUrl(baseUrl)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
            return CardsRepository(retrofit.create(CardsApi::class.java))
        }
    }
}
