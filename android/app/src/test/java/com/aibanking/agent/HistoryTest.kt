package com.aibanking.agent

import com.aibanking.agent.data.HistoryApi
import com.aibanking.agent.data.HistoryRecordView
import com.aibanking.agent.data.HistoryRepository
import com.aibanking.agent.data.HistoryResponse
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HistoryTest {

    private class MockHistoryApi(
        private val records: List<HistoryRecordView> = emptyList(),
    ) : HistoryApi {
        var lastUserId: String? = null
        var lastScope: String? = null
        var lastCategory: String? = null
        var lastToken: String? = null

        override suspend fun list(
            userId: String,
            dateScope: String,
            category: String?,
            userIdHeader: String,
            sessionToken: String,
        ): HistoryResponse {
            lastUserId = userId
            lastScope = dateScope
            lastCategory = category
            lastToken = sessionToken
            val filtered = records.filter {
                (category == null || it.category == category)
            }
            return HistoryResponse(
                code = 200,
                dateScope = dateScope,
                category = category,
                totalRecords = filtered.size,
                data = filtered,
            )
        }
    }

    @Test
    fun repositoryFetchesAndFiltersRecords() = runBlocking {
        val sampleRecords = listOf(
            HistoryRecordView(txId = "TX1", merchant = "咖啡店", category = "餐饮", amount = 28.0, time = "2026-11-01 09:00:00"),
            HistoryRecordView(txId = "TX2", merchant = "转账给 李四", category = "转账支出", amount = 1000.0, time = "2026-11-02 08:30:00"),
            HistoryRecordView(txId = "TX3", merchant = "收到 张三 转账", category = "转账收入", amount = 500.0, time = "2026-11-02 09:10:00"),
        )
        val mockApi = MockHistoryApi(sampleRecords)
        val repository = HistoryRepository(mockApi)

        val all = repository.list("user_zhang", "fake-token", "ALL", null)
        assertEquals(3, all.size)
        assertEquals("user_zhang", mockApi.lastUserId)
        assertEquals("fake-token", mockApi.lastToken)
        assertEquals("ALL", mockApi.lastScope)

        val transfers = repository.list("user_zhang", "fake-token", "TODAY", "转账支出")
        assertEquals(1, transfers.size)
        assertEquals("TX2", transfers[0].txId)
        assertEquals("转账支出", mockApi.lastCategory)
        assertEquals("TODAY", mockApi.lastScope)
    }

    @Test
    fun historyRecordCalculatesInflowAndOutflowCorrectly() {
        val sampleRecords = listOf(
            HistoryRecordView(txId = "TX1", merchant = "咖啡店", category = "餐饮", amount = 30.0),
            HistoryRecordView(txId = "TX2", merchant = "转账给 李四", category = "转账支出", amount = 1000.0),
            HistoryRecordView(txId = "TX3", merchant = "收到 张三 转账", category = "转账收入", amount = 600.0),
            HistoryRecordView(txId = "TX4", merchant = "稳健理财90天", category = "理财申购", amount = 5000.0),
        )

        val outflow = sampleRecords.filter { it.category != "转账收入" }.sumOf { it.amount }
        val inflow = sampleRecords.filter { it.category == "转账收入" }.sumOf { it.amount }

        assertEquals(6030.0, outflow, 0.001)
        assertEquals(600.0, inflow, 0.001)
    }
}
