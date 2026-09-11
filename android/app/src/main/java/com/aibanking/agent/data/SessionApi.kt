package com.aibanking.agent.data

import com.aibanking.agent.BuildConfig
import com.google.gson.annotations.SerializedName
import okhttp3.OkHttpClient
import retrofit2.HttpException
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.POST
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Login against the core banking API.
 *
 * The app authenticates with a password (argon2id verified server-side) and,
 * when the deployment asks for it, an SMS code.  It deliberately does *not* use
 * the demo "issue a session for whoever asks" endpoint: that one is gated on
 * `BANK_DEMO_MODE` precisely so a client cannot quietly keep depending on it.
 */

data class LoginRequest(
    @SerializedName("user_id") val userId: String,
    val password: String,
    val otp: String? = null,
)

data class SessionResponse(
    val code: Int = 0,
    @SerializedName("session_token") val sessionToken: String = "",
    @SerializedName("user_id") val userId: String = "",
    @SerializedName("expires_at") val expiresAt: Long = 0,
    @SerializedName("ttl_seconds") val ttlSeconds: Int = 0,
)

interface SessionApi {
    @POST("api/v1/auth/login")
    suspend fun login(@Body body: LoginRequest): SessionResponse
}

class LoginRepository(private val api: SessionApi) {

    suspend fun login(userId: String, password: String, otp: String?): SessionResponse = try {
        api.login(LoginRequest(userId = userId, password = password, otp = otp?.takeIf { it.isNotBlank() }))
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    companion object {
        fun create(baseUrl: String = BuildConfig.CORE_BASE_URL): LoginRepository {
            val client = OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                // argon2 verification costs tens of milliseconds by design; the
                // timeout leaves room without letting a hung server hold the UI.
                .readTimeout(20, TimeUnit.SECONDS)
                .build()
            val retrofit = Retrofit.Builder()
                .baseUrl(baseUrl)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
            return LoginRepository(retrofit.create(SessionApi::class.java))
        }
    }
}
