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
 * Device enrollment against the core banking API.
 *
 * The app registers the *public* half of the KeyStore key it generated; the
 * private half never leaves the device.  Enrollment is what turns "a liveness
 * challenge was signed" into "signed by a device this account trusts", and it
 * therefore requires a session credential - a self-asserted identity header is
 * not enough to establish a credential.
 */

data class DeviceRegisterRequest(
    @SerializedName("public_key") val publicKey: String,
    val label: String? = null,
    val platform: String? = null,
    @SerializedName("app_version") val appVersion: String? = null,
)

data class DeviceView(
    val code: Int = 0,
    @SerializedName("device_id") val deviceId: String = "",
    @SerializedName("user_id") val userId: String = "",
    val fingerprint: String = "",
    val algorithm: String? = null,
    val status: String = "",
    val label: String? = null,
    @SerializedName("created_at") val createdAt: Long = 0,
    @SerializedName("last_seen_at") val lastSeenAt: Long = 0,
    /** Whether the server holds a delivery token for this device. */
    @SerializedName("push_enabled") val pushEnabled: Boolean = false,
)

/** The FCM registration token this device wants notifications delivered to. */
data class PushTokenRequest(
    val token: String,
    val platform: String? = null,
)

interface DeviceApi {

    @POST("api/v1/devices/register")
    suspend fun register(
        @Body body: DeviceRegisterRequest,
        @Header("X-User-Id") userId: String,
        @Header("X-Session-Token") sessionToken: String,
    ): DeviceView

    @POST("api/v1/devices/{deviceId}/push-token")
    suspend fun registerPushToken(
        @Path("deviceId") deviceId: String,
        @Body body: PushTokenRequest,
        @Header("X-User-Id") userId: String,
        @Header("X-Session-Token") sessionToken: String,
    ): DeviceView

    @GET("api/v1/devices")
    suspend fun list(
        @Header("X-User-Id") userId: String,
        @Header("X-Session-Token") sessionToken: String,
    ): DeviceListResponse

    @POST("api/v1/devices/{deviceId}/revoke")
    suspend fun revoke(
        @Path("deviceId") deviceId: String,
        @Header("X-User-Id") userId: String,
        @Header("X-Session-Token") sessionToken: String,
    ): DeviceView
}

data class DeviceListResponse(val code: Int = 0, val data: List<DeviceView> = emptyList())

class DeviceRepository(private val api: DeviceApi) {

    suspend fun register(
        publicKeySpkiBase64: String,
        principal: String,
        sessionToken: String,
        label: String,
    ): DeviceView = try {
        api.register(
            DeviceRegisterRequest(
                publicKey = publicKeySpkiBase64,
                label = label.take(64),
                platform = "android",
                appVersion = BuildConfig.VERSION_NAME,
            ),
            userId = principal,
            sessionToken = sessionToken,
        )
    } catch (failure: HttpException) {
        val raw = runCatching { failure.response()?.errorBody()?.string() }.getOrNull()
        throw GatewayException(null, "设备注册失败：${raw?.take(160) ?: "HTTP ${failure.code()}"}", failure.code())
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    suspend fun list(principal: String, sessionToken: String): List<DeviceView> = try {
        api.list(userId = principal, sessionToken = sessionToken).data
    } catch (failure: HttpException) {
        throw GatewayException(null, "无法获取设备列表：HTTP ${failure.code()}", failure.code())
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    /**
     * Stop the core trusting this (or another) enrolled device's signatures.
     *
     * Revoking the *current* device is the "this phone is compromised" self-service
     * path: the red tier stops working here immediately, which is the point.
     */
    suspend fun revoke(
        deviceId: String,
        principal: String,
        sessionToken: String,
    ): DeviceView = try {
        api.revoke(deviceId = deviceId, userId = principal, sessionToken = sessionToken)
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    /**
     * Tell the server where to deliver this device's notifications.
     *
     * Only called once a Firebase token exists, which means only when a Firebase
     * project is configured - an unconfigured build registers nothing and the
     * server says `NOT_SENT` with a reason instead of pretending.
     */
    suspend fun registerPushToken(
        deviceId: String,
        token: String,
        principal: String,
        sessionToken: String,
    ): DeviceView = try {
        api.registerPushToken(
            deviceId = deviceId,
            body = PushTokenRequest(token = token, platform = "android"),
            userId = principal,
            sessionToken = sessionToken,
        )
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    companion object {
        fun create(baseUrl: String = BuildConfig.CORE_BASE_URL): DeviceRepository {
            val client = OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(20, TimeUnit.SECONDS)
                .build()
            val retrofit = Retrofit.Builder()
                .baseUrl(baseUrl)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
            return DeviceRepository(retrofit.create(DeviceApi::class.java))
        }
    }
}
