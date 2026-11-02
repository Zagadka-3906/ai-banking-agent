package com.aibanking.agent.ui.chat

import com.aibanking.agent.data.GatewayException
import com.aibanking.agent.data.HistoryRecordView
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.update

// ChatViewModel actions for the history/transactions panel (办理历史 / 业务记录).

fun ChatViewModel.openHistory() {
    _state.update {
        it.copy(
            showHistory = true,
            showCards = false,
            showWealth = false,
            showContacts = false,
            showDevices = false,
            showTasks = false,
            panelBusy = true,
            panelError = null,
        )
    }
    refreshHistory()
}

fun ChatViewModel.closeHistory() = _state.update { it.copy(showHistory = false, selectedHistoryRecord = null) }

fun ChatViewModel.setHistoryDateScope(scope: String) {
    if (_state.value.historyDateScope == scope) return
    _state.update { it.copy(historyDateScope = scope, panelBusy = true, panelError = null) }
    refreshHistory()
}

fun ChatViewModel.setHistoryCategory(category: String?) {
    if (_state.value.historyCategory == category) return
    _state.update { it.copy(historyCategory = category, panelBusy = true, panelError = null) }
    refreshHistory()
}

fun ChatViewModel.selectHistoryRecord(record: HistoryRecordView?) {
    _state.update { it.copy(selectedHistoryRecord = record) }
}

fun ChatViewModel.queryHistoryInChat(record: HistoryRecordView) {
    _state.update {
        it.copy(
            showHistory = false,
            selectedHistoryRecord = null,
        )
    }
    val queryText = "请帮我查询流水单号 ${record.txId}（${record.merchant}，金额 ¥${"%.2f".format(record.amount)}）的详细交易信息"
    sendInternal(queryText)
}

fun ChatViewModel.refreshHistory() {
    val principal = _state.value.principal
    val scope = _state.value.historyDateScope
    val category = _state.value.historyCategory
    sessionWork.launch {
        try {
            val rows = io {
                historyRepo.list(
                    principal = principal,
                    sessionToken = credential().orEmpty(),
                    dateScope = scope,
                    category = category,
                )
            }
            // Sort by time descending (newest first)
            val sorted = rows.sortedByDescending { it.time }
            _state.update { it.copy(historyRecords = sorted, panelBusy = false, panelError = null) }
        } catch (failure: GatewayException) {
            check()
            applyPanelFailure(failure)
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            applyPanelFailure(failure)
        }
    }
}
