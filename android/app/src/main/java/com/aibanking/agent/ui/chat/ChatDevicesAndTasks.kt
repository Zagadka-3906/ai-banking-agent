package com.aibanking.agent.ui.chat

import com.aibanking.agent.data.GatewayException
import com.aibanking.agent.data.SplitRequestView
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.math.BigDecimal

// ChatViewModel actions for the device, Feishu binding and task-centre panels.
// Split out of ChatViewModel.kt; state and session plumbing stay in the class.

// -- device management panel -------------------------------------------

fun ChatViewModel.openDevices() {
    _state.update { it.copy(showDevices = true, showTasks = false, panelBusy = true, feishuBindingCode = null) }
    refreshDevices()
}

fun ChatViewModel.issueFeishuBindingCode() {
    val token = sessionToken ?: return
    _state.update { it.copy(feishuBusy = true, feishuBindingCode = null, error = null) }
    sessionWork.launch {
        try {
            val response = io { agent.issueFeishuBindingCode(token) }
            _state.update { it.copy(feishuBusy = false, feishuBindingCode = response.bindingCode,
                feishuCodeExpiresAtMillis = System.currentTimeMillis() + response.expiresIn * 1000L) }
            delay(response.expiresIn * 1000L)
            _state.update { current ->
                if (current.feishuBindingCode == response.bindingCode) current.copy(feishuBindingCode = null) else current
            }
        } catch (failure: GatewayException) {
            check()
            _state.update { it.copy(feishuBusy = false, error = failure.message) }
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            _state.update { it.copy(feishuBusy = false, error = "无法获取飞书绑定码") }
        }
    }
}

fun ChatViewModel.unbindFeishu() {
    val token = sessionToken ?: return
    _state.update { it.copy(feishuBusy = true, error = null) }
    sessionWork.launch {
        try {
            io { agent.unbindFeishu(token) }
            _state.update { it.copy(feishuBusy = false, feishuBindingCode = null, notice = "飞书绑定已解除") }
        } catch (failure: GatewayException) {
            check()
            _state.update { it.copy(feishuBusy = false, error = failure.message) }
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            _state.update { it.copy(feishuBusy = false, error = "飞书解绑失败") }
        }
    }
}

fun ChatViewModel.refreshDevices() {
    val principal = _state.value.principal
    sessionWork.launch {
        try {
            val rows = io { devices.list(principal, credential().orEmpty()) }
            _state.update { it.copy(devices = rows, panelBusy = false) }
        } catch (failure: GatewayException) {
            check()
            applyPanelFailure(failure)
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            // 解析异常（JsonSyntaxException）等非网关失败同样要收尾：逃出协程会让
            // panelBusy 永远为 true，面板停在骨架屏上，连重试按钮都不出现。
            applyPanelFailure(failure)
        }
    }
}

fun ChatViewModel.revokeDevice(targetId: String) {
    val principal = _state.value.principal
    _state.update { it.copy(panelBusy = true, error = null) }
    sessionWork.launch {
        try {
            io { devices.revoke(targetId, principal, credential().orEmpty()) }
            val rows = io { devices.list(principal, credential().orEmpty()) }
            _state.update {
                it.copy(
                    devices = rows,
                    panelBusy = false,
                    notice = if (targetId == deviceId) "本机已解除注册：红色风险操作在本机不可用" else "设备已停用",
                )
            }
            if (targetId == deviceId) {
                // 本机被解除注册：内存与存储都要忘掉，否则重启后 init 会把它读回来，
                // 应用继续以为本机已注册（红色操作白走一次短信 + 生物识别）。
                deviceId = null
                deviceKeys = null
                sessionStore.clearDeviceId()
            }
        } catch (failure: GatewayException) {
            check()
            applyPanelFailure(failure)
        }
    }
}

// -- task centre panel ---------------------------------------------------

fun ChatViewModel.openTasks() {
    _state.update { it.copy(showTasks = true, showDevices = false, panelBusy = true) }
    refreshTasks()
}

fun ChatViewModel.refreshTasks() {
    val principal = _state.value.principal
    sessionWork.launch {
        try {
            val rows = io { tasks.list(principal, credential().orEmpty()) }
            val splits = io { tasks.splitInbox(principal, credential().orEmpty()) }
            _state.update { it.copy(tasks = rows, splitRequests = splits, panelBusy = false, panelError = null) }
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

fun ChatViewModel.cancelTask(taskId: String) {
    if (_state.value.panelBusy) return
    val principal = _state.value.principal
    _state.update { it.copy(panelBusy = true, taskSubmitting = true, error = null) }
    sessionWork.launch {
        try {
            io { tasks.cancel(taskId, principal, credential().orEmpty()) }
            val rows = io { tasks.list(principal, credential().orEmpty()) }
            _state.update { it.copy(tasks = rows, panelBusy = false, notice = "任务已取消") }
        } catch (failure: GatewayException) {
            check()
            applyPanelFailure(failure)
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            applyPanelFailure(failure)
        } finally {
            check()
            _state.update { it.copy(taskSubmitting = false) }
        }
    }
}

fun ChatViewModel.takeoverTask(taskId: String, reason: String) {
    if (_state.value.panelBusy || reason.isBlank() || reason.length > 300) return
    if (_state.value.tasks.none { it.taskId == taskId && it.status in setOf("PENDING", "WAITING_USER", "FAILED") }) return
    val principal = _state.value.principal
    _state.update { it.copy(panelBusy = true, taskSubmitting = true, error = null, panelError = null) }
    sessionWork.launch {
        try {
            io { tasks.takeover(taskId, reason.trim(), principal, credential().orEmpty()) }
            val rows = io { tasks.list(principal, credential().orEmpty()) }
            _state.update { it.copy(tasks = rows, panelBusy = false, notice = "已暂停关联任务，待人工处理；已完成操作及锁定资金请按回执核对") }
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            applyPanelFailure(failure)
        } finally {
            check()
            _state.update { it.copy(taskSubmitting = false) }
        }
    }
}

fun ChatViewModel.prepareSplitPayment(request: SplitRequestView) {
    if (request.status != "PENDING" || request.share <= 0 || request.fromName.isBlank()) return
    val amount = BigDecimal.valueOf(request.share).stripTrailingZeros().toPlainString()
    _state.update { it.copy(showTasks = false, input = "给${request.fromName}转${amount}元，备注${request.paymentRemark}", notice = "请核对 AA 金额和收款人后发送，付款仍需您确认") }
}

fun ChatViewModel.declineSplit(splitId: String) {
    if (_state.value.panelBusy) return
    val principal = _state.value.principal
    _state.update { it.copy(panelBusy = true, taskSubmitting = true, error = null) }
    sessionWork.launch {
        try {
            io { tasks.declineSplit(splitId, principal, credential().orEmpty()) }
            val splits = io { tasks.splitInbox(principal, credential().orEmpty()) }
            _state.update { it.copy(splitRequests = splits, panelBusy = false, notice = "已拒绝 AA 付款请求") }
        } catch (failure: GatewayException) {
            check()
            applyPanelFailure(failure)
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            applyPanelFailure(failure)
        } finally {
            check()
            _state.update { it.copy(taskSubmitting = false) }
        }
    }
}

fun ChatViewModel.clearPanelError() = _state.update { it.copy(panelError = null) }
