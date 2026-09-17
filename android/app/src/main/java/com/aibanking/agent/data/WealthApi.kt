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
import java.io.IOException
import java.util.concurrent.TimeUnit

data class PerformanceView(
    val period: String = "",
    @SerializedName("product_return_pct") val productReturnPct: Double? = null,
    @SerializedName("benchmark_return_pct") val benchmarkReturnPct: Double? = null,
    val rank: String? = null,
)

data class NavPointView(
    val date: String = "",
    val nav: Double? = null,
    @SerializedName("benchmark_nav") val benchmarkNav: Double? = null,
)

data class AnalysisMetricView(
    val label: String = "",
    val score: Int? = null,
    val description: String? = null,
)

data class ManagerView(
    val name: String? = null,
    @SerializedName("tenure_since") val tenureSince: String? = null,
    @SerializedName("experience_years") val experienceYears: Double? = null,
    val bio: String? = null,
)

data class AllocationView(
    val name: String = "",
    val percentage: Double? = null,
)

/**
 * 理财货架数据（全部为虚构演示产品，业绩比较基准不是收益承诺）。
 *
 * 列表/持仓/风险档案都是只读事实；申购不在客户端发生——页面只把
 * “申购 <代码> <金额>元” 预填进聊天框，真正的下单走 Agent 的红色风险
 * 审批流（短信 + 设备签名），安全边界与对话办理完全一致。
 */

data class ProductView(
    @SerializedName("product_id") val productId: String = "",
    val name: String = "",
    val category: String? = null,
    @SerializedName("risk_level") val riskLevel: String = "",
    val benchmark: String? = null,
    @SerializedName("annualized_yield") val annualizedYield: Double? = null,
    @SerializedName("min_amount") val minAmount: Double = 0.0,
    val liquidity: String? = null,
    val term: String? = null,
    val underlying: String? = null,
    @SerializedName("target_clients") val targetClients: String? = null,
    @SerializedName("data_source") val dataSource: String? = null,
    @SerializedName("as_of_date") val asOfDate: String? = null,
    @SerializedName("latest_nav") val latestNav: Double? = null,
    @SerializedName("daily_change_pct") val dailyChangePct: Double? = null,
    @SerializedName("performance_history") val performanceHistory: List<PerformanceView> = emptyList(),
    @SerializedName("nav_series") val navSeries: List<NavPointView> = emptyList(),
    @SerializedName("analysis_metrics") val analysisMetrics: List<AnalysisMetricView> = emptyList(),
    val manager: ManagerView? = null,
    @SerializedName("asset_allocation") val assetAllocation: List<AllocationView> = emptyList(),
    @SerializedName("established_at") val establishedAt: String? = null,
    @SerializedName("scale_billion") val scaleBillion: Double? = null,
    @SerializedName("manager_company") val managerCompany: String? = null,
    @SerializedName("purchase_rule") val purchaseRule: String? = null,
    @SerializedName("redemption_rule") val redemptionRule: String? = null,
    @SerializedName("confirm_time") val confirmTime: String? = null,
    @SerializedName("fee_rate") val feeRate: Double? = null,
    val summary: String? = null,
    val announcement: String? = null,
)

data class ProductListResponse(val code: Int = 0, val data: List<ProductView> = emptyList())

data class HoldingView(
    @SerializedName("holding_id") val holdingId: String = "",
    @SerializedName("product_id") val productId: String = "",
    @SerializedName("product_name") val productName: String = "",
    val principal: Double = 0.0,
    @SerializedName("estimated_value") val estimatedValue: Double = 0.0,
    @SerializedName("purchased_at") val purchasedAt: String? = null,
)

data class HoldingListResponse(val code: Int = 0, val data: List<HoldingView> = emptyList())

data class RiskProfileView(
    @SerializedName("risk_profile") val riskProfile: String = "",
    @SerializedName("assessed_at") val assessedAt: String? = null,
    val source: String? = null,
)

data class RiskProfileResponse(val code: Int = 0, val data: RiskProfileView? = null)

interface WealthApi {

    @GET("api/v1/wealth/products")
    suspend fun products(
        @Header("X-User-Id") userId: String,
        @Header("X-Session-Token") sessionToken: String,
    ): ProductListResponse

    @GET("api/v1/wealth/holdings/{userId}")
    suspend fun holdings(
        @Path("userId") userId: String,
        @Header("X-User-Id") userIdHeader: String,
        @Header("X-Session-Token") sessionToken: String,
    ): HoldingListResponse

    @GET("api/v1/wealth/risk-profile")
    suspend fun riskProfile(
        @Header("X-User-Id") userId: String,
        @Header("X-Session-Token") sessionToken: String,
    ): RiskProfileResponse
}

class WealthRepository(private val api: WealthApi) {

    suspend fun products(principal: String, sessionToken: String): List<ProductView> = try {
        api.products(userId = principal, sessionToken = sessionToken).data
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    suspend fun holdings(principal: String, sessionToken: String): List<HoldingView> = try {
        api.holdings(userId = principal, userIdHeader = principal, sessionToken = sessionToken).data
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    suspend fun riskProfile(principal: String, sessionToken: String): RiskProfileView? = try {
        api.riskProfile(userId = principal, sessionToken = sessionToken).data
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    companion object {
        fun create(baseUrl: String = BuildConfig.CORE_BASE_URL): WealthRepository {
            val client = OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(20, TimeUnit.SECONDS)
                .build()
            val retrofit = Retrofit.Builder()
                .baseUrl(baseUrl)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
            return WealthRepository(retrofit.create(WealthApi::class.java))
        }
    }
}
