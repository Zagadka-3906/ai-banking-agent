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

data class LogoutResponse(
    val code: Int = 0,
    val status: String = "",
    @SerializedName("user_id") val userId: String? = null,
)

/** Who the core says a presented session belongs to. */
data class IdentityResponse(
    val code: Int = 0,
    @SerializedName("user_id") val userId: String = "",
)

/**
 * The outcome of asking the core whether a *stored* session still works.
 *
 * "Expired" and "Unreachable" must stay distinct: the first means the credential
 * is dead and the user has to log in again, the second means we could not ask -
 * and logging someone out because their train went into a tunnel would be worse
 * than showing them a stale header.
 */
sealed interface SessionCheck {
    data class Valid(val userId: String) : SessionCheck
    object Expired : SessionCheck
    object Unreachable : SessionCheck
}

interface SessionApi {
    @POST("api/v1/auth/login")
    suspend fun login(@Body body: LoginRequest): SessionResponse

    /** Revokes exactly the presented session server-side (see core /auth/logout). */
    @POST("api/v1/auth/logout")
    suspend fun logout(@Header("X-Session-Token") sessionToken: String): LogoutResponse

    /** Resolves a session to its subject; 401 once the core no longer honours it. */
    @GET("api/v1/auth/session")
    suspend fun identity(@Header("X-Session-Token") sessionToken: String): IdentityResponse
}

class LoginRepository(private val api: SessionApi) {

    suspend fun login(userId: String, password: String, otp: String?): SessionResponse = try {
        api.login(LoginRequest(userId = userId, password = password, otp = otp?.takeIf { it.isNotBlank() }))
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    /**
     * Kill the credential on the server before the client forgets it.
     *
     * Best effort by design: a logout must succeed even when the network is
     * gone or the session already expired, so failures are swallowed here -
     * the local wipe in the ViewModel happens regardless, and an unrevoked
     * token still dies at its (short) expiry.
     */
    suspend fun logout(sessionToken: String?): Boolean {
        val token = sessionToken?.takeIf { it.isNotBlank() } ?: return false
        return try {
            api.logout(token).code == 200
        } catch (failure: HttpException) {
            // An already-expired/unknown session answers 401; that is logout
            // succeeding in every way that matters.
            failure.code() == 401
        } catch (failure: IOException) {
            false
        }
    }

    /**
     * Ask the core whether a stored session still works.
     *
     * A stored credential outlives the server that issued it: the core keeps
     * sessions in memory, so a restart invalidates every one of them.  Trusting
     * the stored token at start-up showed the user a logged-in screen whose every
     * action failed - the first turn came back 401 and dumped them back to the
     * login form with the instruction they had just typed already sent.  Asking
     * once at start-up turns that into an honest login screen.
     */
    suspend fun check(sessionToken: String): SessionCheck = try {
        val identity = api.identity(sessionToken)
        val subject = identity.userId.trim()
        if (subject.isEmpty()) SessionCheck.Expired else SessionCheck.Valid(subject)
    } catch (failure: HttpException) {
        if (failure.code() == 401 || failure.code() == 403) SessionCheck.Expired else SessionCheck.Unreachable
    } catch (failure: IOException) {
        // Offline is not proof of expiry: keep the session and let the next real
        // call decide.
        SessionCheck.Unreachable
    }

    companion object {
        fun create(baseUrl: String = BuildConfig.CORE_BASE_URL): LoginRepository {
            if (BuildConfig.STANDALONE_MODE) {
                return LoginRepository(com.aibanking.agent.data.standalone.StandaloneBank.sessionApi)
            }
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
