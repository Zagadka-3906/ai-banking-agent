package com.aibanking.agent.ui.chat

import android.os.SystemClock
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.runtime.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.withTimeoutOrNull

/** State consumption and snackbar display have separate lifetimes; undo preempts the regular queue. */
@Composable
internal fun ChatMessageEffects(
    error: String?, notice: String?, undo: PendingContactDeletion?,
    snackbar: SnackbarHostState, onConsumed: (String?, String?) -> Unit, onUndo: (String) -> Unit,
    owner: String = "",
) {
    val messages = remember(owner) { Channel<String>(Channel.UNLIMITED) }
    var currentMessage by remember(owner) { mutableStateOf<String?>(null) }
    val undoAction by rememberUpdatedState(onUndo)
    DisposableEffect(messages) { onDispose { messages.cancel() } }
    LaunchedEffect(error, notice, owner) {
        if (error == null && notice == null) return@LaunchedEffect
        val message = error ?: notice?.takeUnless {
            it.startsWith("会话有效期") || it == "本机设备已注册，高风险操作可用"
        }
        onConsumed(error, notice)
        if (message != null) messages.trySend(message)
    }
    LaunchedEffect(snackbar, owner, undo?.contact?.contactId) {
        if (undo != null) return@LaunchedEffect
        while (true) {
            val message = currentMessage ?: messages.receive().also { currentMessage = it }
            snackbar.showSnackbar(message)
            currentMessage = null
        }
    }
    LaunchedEffect(snackbar, owner, undo?.contact?.contactId) {
        val deletion = undo ?: return@LaunchedEffect
        val remaining = deletion.deadlineMillis - SystemClock.elapsedRealtime()
        if (remaining <= 0) return@LaunchedEffect
        val result = withTimeoutOrNull(remaining) {
            snackbar.showSnackbar("已移出联系人 ${deletion.contact.name}，5 秒内可撤销",
                actionLabel = "撤销", duration = SnackbarDuration.Indefinite)
        }
        if (result == SnackbarResult.ActionPerformed) undoAction(deletion.contact.contactId)
    }
}
