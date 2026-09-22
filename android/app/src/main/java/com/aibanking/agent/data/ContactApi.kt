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
 * Contact book against the core banking API.
 *
 * Contacts the user saves here become first-class payees: the core resolves a
 * transfer by the saved name or phone exactly like its built-in allow-list.
 * A phone that belongs to an existing account links to it; anything else gets
 * a server-side shadow payee account, so "add 王五, then 给王五转 300 元" settles
 * end to end without any client-side account fabrication.
 */

data class ContactView(
    @SerializedName("contact_id") val contactId: String = "",
    val name: String = "",
    val phone: String = "",
    val relationship: String = "",
    @SerializedName("relationship_label") val relationshipLabel: String = "",
    val birthday: String? = null,
    @SerializedName("account_id") val accountId: String? = null,
    @SerializedName("linked_existing") val linkedExisting: Boolean = false,
    @SerializedName("account_name") val accountName: String? = null,
    @SerializedName("created_at") val createdAt: String? = null,
)

data class ContactListResponse(val code: Int = 0, val data: List<ContactView> = emptyList())

data class ContactAddRequest(
    val name: String,
    val phone: String,
    val relationship: String,
    val birthday: String? = null,
)

data class ContactAddResponse(val code: Int = 0, val status: String = "", val data: ContactView? = null)

data class ContactRemoveResponse(val code: Int = 0, val status: String = "")

interface ContactApi {

    @GET("api/v1/contacts/{userId}/book")
    suspend fun list(
        @Path("userId") userId: String,
        @Header("X-User-Id") userIdHeader: String,
        @Header("X-Session-Token") sessionToken: String,
    ): ContactListResponse

    @POST("api/v1/contacts/{userId}/book")
    suspend fun add(
        @Path("userId") userId: String,
        @Body body: ContactAddRequest,
        @Header("X-User-Id") userIdHeader: String,
        @Header("X-Session-Token") sessionToken: String,
        // 核心按 (用户, 端点, 键) 缓存响应：同一个键重发只会回放上一次的结果，
        // 超时重试不会建出第二个联系人。
        @Header("Idempotency-Key") idempotencyKey: String,
    ): ContactAddResponse

    @POST("api/v1/contacts/{userId}/book/{contactId}/remove")
    suspend fun remove(
        @Path("userId") userId: String,
        @Path("contactId") contactId: String,
        @Header("X-User-Id") userIdHeader: String,
        @Header("X-Session-Token") sessionToken: String,
    ): ContactRemoveResponse
}

class ContactRepository(private val api: ContactApi) {

    suspend fun list(principal: String, sessionToken: String): List<ContactView> = try {
        api.list(userId = principal, userIdHeader = principal, sessionToken = sessionToken).data
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    /**
     * Save one contact.
     *
     * [idempotencyKey] is required rather than defaulted on purpose: a fresh key
     * per call would make a read timeout plus an explicit retry create a second
     * contact.  The caller owns one key per user submission and reuses it for
     * every retry of that submission.
     */
    suspend fun add(
        principal: String,
        sessionToken: String,
        name: String,
        phone: String,
        relationship: String,
        birthday: String?,
        idempotencyKey: String,
    ): ContactView = try {
        api.add(
            userId = principal,
            body = ContactAddRequest(name = name, phone = phone, relationship = relationship, birthday = birthday?.takeIf { it.isNotBlank() }),
            userIdHeader = principal,
            sessionToken = sessionToken,
            idempotencyKey = idempotencyKey,
        ).data ?: throw GatewayException(null, "核心服务未返回联系人", 502)
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    suspend fun remove(contactId: String, principal: String, sessionToken: String) = try {
        api.remove(userId = principal, contactId = contactId, userIdHeader = principal, sessionToken = sessionToken)
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    companion object {
        fun create(baseUrl: String = BuildConfig.CORE_BASE_URL): ContactRepository {
            if (BuildConfig.STANDALONE_MODE) {
                return ContactRepository(com.aibanking.agent.data.standalone.StandaloneBank.contactApi)
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
            return ContactRepository(retrofit.create(ContactApi::class.java))
        }
    }
}
