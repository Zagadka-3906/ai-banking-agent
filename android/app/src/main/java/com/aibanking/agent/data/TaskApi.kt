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
 * Task-centre access against the core banking API.
 *
 * The task store lives entirely server-side; the app only reads it and cancels
 * items that have not run yet.  Scheduled money movement can never be executed
 * from here: when a task falls due the core moves it to WAITING_USER and it
 * still needs a fresh approval, so a task list is a mirror, not a second
 * execution path.
 */

data class TaskView(
    @SerializedName("task_id") val taskId: String = "",
    @SerializedName("task_type") val taskType: String = "",
    val status: String = "",
    @SerializedName("run_at") val runAt: String? = null,
    @SerializedName("created_at") val createdAt: String? = null,
    @SerializedName("updated_at") val updatedAt: String? = null,
    val error: String? = null,
    @SerializedName("depends_on") val dependsOn: List<String> = emptyList(),
    val compensation: String? = null,
    /** Domain payload (schedule/split/reminder details); rendered defensively. */
    val payload: Map<String, Any?> = emptyMap(),
)

data class TaskListResponse(val code: Int = 0, val data: List<TaskView> = emptyList())

data class TaskActionRequest(val reason: String = "用户请求")

data class TaskActionResponse(val code: Int = 0, val data: TaskView? = null)

interface TaskApi {

    @GET("api/v1/tasks")
    suspend fun list(
        @Header("X-User-Id") userId: String,
        @Header("X-Session-Token") sessionToken: String,
    ): TaskListResponse

    @POST("api/v1/tasks/{taskId}/cancel")
    suspend fun cancel(
        @Path("taskId") taskId: String,
        @Body body: TaskActionRequest,
        @Header("X-User-Id") userId: String,
        @Header("X-Session-Token") sessionToken: String,
    ): TaskActionResponse
}

class TaskRepository(private val api: TaskApi) {

    suspend fun list(principal: String, sessionToken: String): List<TaskView> = try {
        api.list(userId = principal, sessionToken = sessionToken).data
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    suspend fun cancel(taskId: String, principal: String, sessionToken: String): TaskView = try {
        api.cancel(taskId = taskId, body = TaskActionRequest(), userId = principal, sessionToken = sessionToken).data
            ?: throw GatewayException(null, "核心服务未返回任务状态", 502)
    } catch (failure: HttpException) {
        throw failure.asGatewayException()
    } catch (failure: IOException) {
        throw GatewayException(null, "无法连接核心服务：${failure.message ?: "网络错误"}", 0)
    }

    companion object {
        fun create(baseUrl: String = BuildConfig.CORE_BASE_URL): TaskRepository {
            val client = OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(20, TimeUnit.SECONDS)
                .build()
            val retrofit = Retrofit.Builder()
                .baseUrl(baseUrl)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
            return TaskRepository(retrofit.create(TaskApi::class.java))
        }
    }
}
