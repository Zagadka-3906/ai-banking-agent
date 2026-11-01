package com.aibanking.agent.ui.chat

import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.*
import kotlinx.coroutines.channels.Channel

/** State consumption and snackbar display have separate lifetimes. */
@Composable
internal fun ChatMessageEffects(
    error: String?, notice: String?,
    snackbar: SnackbarHostState, onConsumed: (String?, String?) -> Unit,
    owner: String = "",
) {
    val messages = remember(owner) { Channel<String>(Channel.UNLIMITED) }
    var currentMessage by remember(owner) { mutableStateOf<String?>(null) }
    DisposableEffect(messages) { onDispose { messages.cancel() } }
    LaunchedEffect(error, notice, owner) {
        if (error == null && notice == null) return@LaunchedEffect
        val message = error ?: notice?.takeUnless {
            it.startsWith("会话有效期") || it == "本机设备已注册，高风险操作可用"
        }
        onConsumed(error, notice)
        if (message != null) messages.trySend(message)
    }
    LaunchedEffect(snackbar, owner) {
        while (true) {
            val message = currentMessage ?: messages.receive().also { currentMessage = it }
            snackbar.showSnackbar(message)
            currentMessage = null
        }
    }
}
