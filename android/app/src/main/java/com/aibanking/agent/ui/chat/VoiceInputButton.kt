package com.aibanking.agent.ui.chat

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner

/** Only on-device recognition: bank requests are never silently sent to ASR servers. */
@Composable
internal fun VoiceInputButton(enabled: Boolean, captureResult: () -> (String) -> Unit) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    var recognizer by remember { mutableStateOf<SpeechRecognizer?>(null) }
    var listening by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var pendingResult by remember { mutableStateOf<((String) -> Unit)?>(null) }
    val available = remember { Build.VERSION.SDK_INT >= 31 && runCatching { SpeechRecognizer.isOnDeviceRecognitionAvailable(context) }.getOrDefault(false) }
    val start = {
        if (enabled && available && pendingResult != null && Build.VERSION.SDK_INT >= 31) {
            try {
                recognizer?.destroy()
                val result = pendingResult
                val engine = SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
                recognizer = engine
                engine.setRecognitionListener(object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) { if (recognizer === engine) listening = true }
                    override fun onBeginningOfSpeech() = Unit
                    override fun onRmsChanged(rmsdB: Float) = Unit
                    override fun onBufferReceived(buffer: ByteArray?) = Unit
                    override fun onEndOfSpeech() { if (recognizer === engine) listening = false }
                    override fun onError(error: Int) {
                        if (recognizer !== engine || !lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) return
                        listening = false; message = "未能识别，请重试或输入文字"
                    }
                    override fun onResults(results: Bundle?) {
                        if (recognizer !== engine || !lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) return
                        listening = false
                        results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.takeIf { it.isNotBlank() }?.let { result?.invoke(it) }
                        message = "请核对识别文字，编辑后点击发送"
                    }
                    override fun onPartialResults(partialResults: Bundle?) = Unit
                    override fun onEvent(eventType: Int, params: Bundle?) = Unit
                })
                engine.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
                    putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
                })
                listening = true
                message = null
            } catch (_: Exception) { listening = false; message = "本机语音服务不可用，请输入文字" }
        }
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted && pendingResult != null && lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) start()
        else { pendingResult = null; message = if (granted) "麦克风权限已允许，请点击语音开始识别" else "未启用麦克风，可继续输入文字" }
    }
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_STOP) { val previous = recognizer; recognizer = null; previous?.cancel(); previous?.destroy(); pendingResult = null; listening = false }
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer); recognizer?.destroy(); recognizer = null }
    }
    LaunchedEffect(enabled) { if (!enabled) { val previous = recognizer; recognizer = null; previous?.cancel(); previous?.destroy(); listening = false; pendingResult = null } }
    TextButton(enabled = enabled, colors = androidx.compose.material3.ButtonDefaults.textButtonColors(contentColor = androidx.compose.material3.MaterialTheme.colorScheme.onSurface), modifier = Modifier.testTag("voice_input"), onClick = {
        if (listening) { recognizer?.stopListening(); listening = false }
        else if (!available) message = "本设备不支持端侧语音识别，请输入文字"
        else { pendingResult = captureResult(); permission.launch(Manifest.permission.RECORD_AUDIO) }
    }) { Text(if (listening) "停止" else "语音") }
    message?.let { text ->
        androidx.compose.material3.AlertDialog(onDismissRequest = { message = null },
            title = { Text("语音输入") }, text = { Text(text) },
            confirmButton = { TextButton(colors = androidx.compose.material3.ButtonDefaults.textButtonColors(contentColor = androidx.compose.material3.MaterialTheme.colorScheme.onSurface), onClick = { message = null }) { Text("知道了") } })
    }
}
