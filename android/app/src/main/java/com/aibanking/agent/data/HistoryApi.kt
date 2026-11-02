package com.aibanking.agent.data

import com.aibanking.agent.BuildConfig
import com.google.gson.annotations.SerializedName
import okhttp3.OkHttpClient
import retrofit2.HttpException
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.Path
import retrofit2.http.Query
import java.io.IOException
import java.util.concurrent.TimeUnit

/** 业务办理/交易流水记录视图模型。 */
data class HistoryRecordView(
    @SerializedName("tx_id") val txId: String = "",
    @SerializedName("account_id") val accountId: String = "",
    @SerializedName("merchant") val merchant: String = "",
    @SerializedName("category") val category: String = "",
    @SerializedName("amount") val amount: Double = 0.0,
    @SerializedName("time") val time: String = "",
    @SerializedName("remark") val remark: String? = null,
    @SerializedName("counterparty_id") val counterpartyId: String? = null,
    @SerializedName("card_id") val cardId: String? = null,
    @SerializedName("country") val country: String? = null,
)

data class HistoryResponse(
    val code: Int = 0,
    @SerializedName("date_scope") val dateScope: String = "ALL",
    val category: String? = null,
    @SerializedName("total_records") val totalRecords: Int = 0,
    val data: List<HistoryRecordView> = emptyList(),
)

interface HistoryApi {

    @GET("api/v1/accounts/{userId}/transactions")
    suspend fun list(
        @Path("userId") userId: String,
        @Query("date_scope") dateScope: String = "ALL",
        @Query("category") category: String? = null,
        @Header("X-User-Id") userIdHeader: String,
        @Header("X-Session-Token") sessionToken: String,
    ): HistoryResponse
}

class HistoryRepository(private val api: HistoryApi) {

    suspend fun list(
        principal: String,
        sessionToken: String,
        dateScope: String = "ALL",
        category: String? = null,
    ): List<HistoryRecordView> = try {
        api.list(
            userId = principal,
            dateScope = dateScope,
            category = category?.takeIf { it.isNotBlank() },
            userIdHeader = principal,
            sessionToken = sessionToken,
        ).data
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    companion object {
        fun create(baseUrl: String = BuildConfig.CORE_BASE_URL): HistoryRepository {
            val client = OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(20, TimeUnit.SECONDS)
                .build()
            val retrofit = Retrofit.Builder()
                .baseUrl(baseUrl)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
            return HistoryRepository(retrofit.create(HistoryApi::class.java))
        }
    }
}
