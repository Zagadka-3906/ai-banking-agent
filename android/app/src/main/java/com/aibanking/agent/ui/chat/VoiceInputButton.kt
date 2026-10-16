package com.aibanking.agent.ui.chat

import android.Manifest
import android.content.Context
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
import androidx.core.content.edit
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import kotlinx.coroutines.launch

private const val VOICE_PREFS = "voice_input"
private const val CLOUD_CONSENT_KEY = "cloud_asr_consent"

private enum class VoiceMode { DEVICE, CLOUD }

/**
 * Voice input that only ever fills the input box for review.
 *
 * On-device recognition is preferred: the audio never leaves the phone. Only
 * when the device has no recogniser, and the gateway has cloud recognition
 * configured, does the user get the option to upload a clip - after an
 * explicit, remembered consent that says where the audio goes.
 */
@Composable
internal fun VoiceInputButton(
    enabled: Boolean,
    captureResult: () -> (String) -> Unit,
    cloudAvailable: ((Boolean) -> Unit) -> Unit = { it(false) },
    transcribeCloud: (ByteArray, (String) -> Unit, (String) -> Unit) -> Unit = { _, _, fail -> fail("云端语音识别不可用") },
) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val scope = rememberCoroutineScope()
    val prefs = remember { context.getSharedPreferences(VOICE_PREFS, Context.MODE_PRIVATE) }
    var recognizer by remember { mutableStateOf<SpeechRecognizer?>(null) }
    var recorder by remember { mutableStateOf<WavRecorder?>(null) }
    var listening by remember { mutableStateOf(false) }
    var uploading by remember { mutableStateOf(false) }
    var askConsent by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var pendingResult by remember { mutableStateOf<((String) -> Unit)?>(null) }
    var pendingMode by remember { mutableStateOf(VoiceMode.DEVICE) }
    val onDevice = remember { Build.VERSION.SDK_INT >= 31 && runCatching { SpeechRecognizer.isOnDeviceRecognitionAvailable(context) }.getOrDefault(false) }

    fun startOnDevice() {
        if (Build.VERSION.SDK_INT < 31) return
        if (!(enabled && onDevice && pendingResult != null)) return
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

    fun finishCloudRecording() {
        val active = recorder ?: return
        recorder = null
        listening = false
        val wav = active.stop()
        val deliver = pendingResult
        pendingResult = null
        if (wav == null || deliver == null) { message = "没有录到声音，请重试或输入文字"; return }
        uploading = true
        transcribeCloud(wav, { text ->
            uploading = false
            if (lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) { deliver(text); message = "请核对识别文字，编辑后点击发送" }
        }, { failure -> uploading = false; message = failure })
    }

    fun startCloud() {
        if (!enabled || pendingResult == null) return
        val active = WavRecorder(onLimit = { scope.launch { finishCloudRecording() } })
        if (!active.start()) { pendingResult = null; message = "无法打开麦克风，请输入文字"; return }
        recorder = active
        listening = true
        message = null
    }

    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted && pendingResult != null && lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) {
            if (pendingMode == VoiceMode.CLOUD) startCloud() else startOnDevice()
        } else { pendingResult = null; message = if (granted) "麦克风权限已允许，请点击语音开始识别" else "未启用麦克风，可继续输入文字" }
    }

    fun beginCloud() {
        pendingMode = VoiceMode.CLOUD
        pendingResult = captureResult()
        permission.launch(Manifest.permission.RECORD_AUDIO)
    }

    fun cancelAll() {
        val previous = recognizer; recognizer = null; previous?.cancel(); previous?.destroy()
        recorder?.cancel(); recorder = null
        listening = false; pendingResult = null
    }

    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_STOP) cancelAll() }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer); recognizer?.destroy(); recognizer = null; recorder?.cancel(); recorder = null }
    }
    LaunchedEffect(enabled) { if (!enabled) cancelAll() }

    TextButton(enabled = enabled && !uploading, colors = androidx.compose.material3.ButtonDefaults.textButtonColors(contentColor = androidx.compose.material3.MaterialTheme.colorScheme.onSurface), modifier = Modifier.testTag("voice_input"), onClick = {
        when {
            recorder != null -> finishCloudRecording()
            listening -> { recognizer?.stopListening(); listening = false }
            onDevice -> { pendingMode = VoiceMode.DEVICE; pendingResult = captureResult(); permission.launch(Manifest.permission.RECORD_AUDIO) }
            else -> cloudAvailable { available ->
                when {
                    !available -> message = "本设备不支持端侧语音识别，请输入文字"
                    prefs.getBoolean(CLOUD_CONSENT_KEY, false) -> beginCloud()
                    else -> askConsent = true
                }
            }
        }
    }) { Text(when { uploading -> "识别中"; listening -> "停止"; else -> "语音" }) }

    if (askConsent) {
        androidx.compose.material3.AlertDialog(onDismissRequest = { askConsent = false },
            title = { Text("使用云端语音识别") },
            text = { Text("本设备没有端侧语音识别。若继续，您说的这段话（最长 $SPEECH_MAX_SECONDS 秒）会经本机网关发送到阿里云百炼语音识别服务转成文字；录音不保存在手机上，识别结果只填入输入框，由您核对后再发送。转账等操作仍需在确认卡上确认。") },
            confirmButton = { TextButton(modifier = Modifier.testTag("voice_cloud_consent"), onClick = {
                askConsent = false; prefs.edit { putBoolean(CLOUD_CONSENT_KEY, true) }; beginCloud()
            }) { Text("同意并录音") } },
            dismissButton = { TextButton(onClick = { askConsent = false; message = "已取消，可继续输入文字" }) { Text("不同意") } })
    }
    message?.let { text ->
        androidx.compose.material3.AlertDialog(onDismissRequest = { message = null },
            title = { Text("语音输入") }, text = { Text(text) },
            confirmButton = { TextButton(colors = androidx.compose.material3.ButtonDefaults.textButtonColors(contentColor = androidx.compose.material3.MaterialTheme.colorScheme.onSurface), onClick = { message = null }) { Text("知道了") } })
    }
}
