package com.aibanking.agent.ui.chat

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.core.content.ContextCompat
import androidx.core.content.edit
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val VOICE_PREFS = "voice_input"
private const val CLOUD_CONSENT_KEY = "cloud_asr_consent"
private const val MIN_RECORD_MILLIS = 600L
private const val WAVE_BARS = 40

private enum class VoiceEngine { DEVICE, CLOUD }

internal enum class VoicePhase { IDLE, RECORDING, RECOGNIZING }

/** Dictation only. Audio is transient; recognized text is reviewed in the composer. */
@Stable
internal class VoiceInput {
    var voiceMode by mutableStateOf(false)
    var phase by mutableStateOf(VoicePhase.IDLE)
    var cloudRecognition by mutableStateOf(false)
    var partialText by mutableStateOf("")
    var elapsedSeconds by mutableIntStateOf(0)
    var level by mutableFloatStateOf(0f)
    val wave = mutableStateListOf<Float>().apply { repeat(WAVE_BARS) { add(0f) } }
    var message by mutableStateOf<String?>(null)
    var askConsent by mutableStateOf(false)

    internal var onToggle: () -> Unit = {}
    internal var onStart: () -> Unit = {}
    internal var onFinish: () -> Unit = {}
    internal var onCancel: () -> Unit = {}
    internal var onClose: () -> Unit = {}

    fun toggle() = onToggle()
    fun start() = onStart()
    fun finish() = onFinish()
    fun cancel() = onCancel()
    fun close() = onClose()
}

@Composable
internal fun rememberVoiceInput(
    enabled: Boolean,
    captureResult: () -> (String) -> Unit,
    cloudAvailable: ((Boolean) -> Unit) -> Unit = { it(false) },
    transcribeCloud: (ByteArray, (String) -> Unit, (String) -> Unit) -> Unit = { _, _, fail -> fail("云端语音识别不可用") },
    audioRecorderFactory: (() -> Unit) -> VoiceAudioCapture = ::WavRecorder,
    onDeviceAvailable: (Context) -> Boolean = { context ->
        Build.VERSION.SDK_INT >= 31 && runCatching { SpeechRecognizer.isOnDeviceRecognitionAvailable(context) }.getOrDefault(false)
    },
): VoiceInput {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val scope = rememberCoroutineScope()
    val prefs = remember { context.getSharedPreferences(VOICE_PREFS, Context.MODE_PRIVATE) }
    val voice = remember { VoiceInput() }
    val currentEnabled by rememberUpdatedState(enabled)
    val onDevice = remember { onDeviceAvailable(context) }
    val engine = remember { mutableStateOf(VoiceEngine.DEVICE) }
    val recognizer = remember { mutableStateOf<SpeechRecognizer?>(null) }
    val recorder = remember { mutableStateOf<VoiceAudioCapture?>(null) }
    val pendingResult = remember { mutableStateOf<((String) -> Unit)?>(null) }
    val earlyText = remember { mutableStateOf<String?>(null) }
    val startedAt = remember { mutableStateOf(0L) }
    val generation = remember { mutableStateOf(0L) }
    val alive = remember { mutableStateOf(true) }

    fun resumed() = lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)
    fun granted() = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    fun reset() {
        voice.phase = VoicePhase.IDLE
        voice.partialText = ""
        voice.level = 0f
        voice.elapsedSeconds = 0
        voice.wave.indices.forEach { voice.wave[it] = 0f }
        pendingResult.value = null
        earlyText.value = null
    }

    fun cancelAll() {
        generation.value++
        val previous = recognizer.value; recognizer.value = null
        runCatching { previous?.cancel() }
        runCatching { previous?.destroy() }
        recorder.value?.cancel(); recorder.value = null
        reset()
    }

    /** The text goes into the input box for review; the keyboard comes back with it. */
    fun deliver(text: String) {
        val target = pendingResult.value
        reset()
        if (!resumed()) return
        if (text.isBlank()) { voice.message = "没有听清，请再说一次"; return }
        target?.invoke(text)
        voice.voiceMode = false
    }

    fun startDevice() {
        if (Build.VERSION.SDK_INT < 31) return
        try {
            recognizer.value?.destroy()
            val engineInstance = SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
            recognizer.value = engineInstance
            engineInstance.setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) = Unit
                override fun onBeginningOfSpeech() = Unit
                override fun onRmsChanged(rmsdB: Float) { if (recognizer.value === engineInstance) voice.level = ((rmsdB + 2f) / 12f).coerceIn(0f, 1f) }
                override fun onBufferReceived(buffer: ByteArray?) = Unit
                override fun onEndOfSpeech() = Unit
                override fun onError(error: Int) {
                    if (recognizer.value !== engineInstance || !resumed()) return
                    recognizer.value = null; engineInstance.destroy()
                    if (voice.phase == VoicePhase.RECORDING && earlyText.value != null) return
                    if (voice.phase != VoicePhase.IDLE) { reset(); voice.message = "没有听清，请再说一次" }
                }
                override fun onResults(results: Bundle?) {
                    if (recognizer.value !== engineInstance || !resumed()) return
                    val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()
                    recognizer.value = null; engineInstance.destroy()
                    // On-device recognition may finish on silence before the user taps Done.
                    if (voice.phase == VoicePhase.RECORDING) {
                        earlyText.value = text
                        voice.partialText = text
                    } else deliver(text)
                }
                override fun onPartialResults(partialResults: Bundle?) {
                    if (recognizer.value === engineInstance && voice.phase == VoicePhase.RECORDING && resumed()) {
                        voice.partialText = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()
                    }
                }
                override fun onEvent(eventType: Int, params: Bundle?) = Unit
            })
            engineInstance.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
                putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            })
        } catch (_: Exception) { cancelAll(); voice.message = "本机语音服务不可用，请输入文字" }
    }

    fun startCloud() {
        val capture = generation.value
        val active = audioRecorderFactory { scope.launch {
            if (generation.value == capture && voice.phase == VoicePhase.RECORDING) voice.finish()
        } }
        if (!active.start()) { cancelAll(); voice.message = "无法打开麦克风，请输入文字"; return }
        recorder.value = active
    }

    fun finishCloud() {
        val active = recorder.value ?: return
        recorder.value = null
        val wav = active.stop()
        if (wav == null) { reset(); voice.message = "没有录到声音，请再说一次"; return }
        val request = generation.value
        fun current() = alive.value && resumed() && generation.value == request && voice.phase == VoicePhase.RECOGNIZING
        transcribeCloud(wav, { text -> if (current()) deliver(text) }, { failure ->
            if (current()) { reset(); voice.message = failure }
        })
    }

    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (alive.value && resumed()) {
            if (ok && currentEnabled) voice.voiceMode = true else if (!ok) voice.message = "未启用麦克风，可继续输入文字"
        }
    }

    fun enterVoiceMode(useEngine: VoiceEngine) {
        if (!alive.value || !resumed() || !currentEnabled) return
        engine.value = useEngine
        voice.cloudRecognition = useEngine == VoiceEngine.CLOUD
        if (granted()) voice.voiceMode = true else permission.launch(Manifest.permission.RECORD_AUDIO)
    }

    voice.onToggle = {
        when {
            voice.voiceMode -> { cancelAll(); voice.voiceMode = false }
            !enabled -> Unit
            onDevice -> enterVoiceMode(VoiceEngine.DEVICE)
            else -> cloudAvailable { available ->
                if (!alive.value || !resumed() || !currentEnabled) return@cloudAvailable
                when {
                    !available -> voice.message = "本设备不支持端侧语音识别，请输入文字"
                    prefs.getBoolean(CLOUD_CONSENT_KEY, false) -> enterVoiceMode(VoiceEngine.CLOUD)
                    else -> voice.askConsent = true
                }
            }
        }
    }
    voice.onStart = start@{
        if (!enabled || !voice.voiceMode || !resumed() || voice.phase != VoicePhase.IDLE) return@start
        if (!granted()) { permission.launch(Manifest.permission.RECORD_AUDIO); return@start }
        generation.value++
        pendingResult.value = captureResult()
        earlyText.value = null
        voice.partialText = ""
        voice.elapsedSeconds = 0
        voice.wave.indices.forEach { voice.wave[it] = 0f }
        startedAt.value = SystemClock.elapsedRealtime()
        voice.phase = VoicePhase.RECORDING
        if (engine.value == VoiceEngine.CLOUD) startCloud() else startDevice()
    }
    voice.onFinish = finish@{
        if (voice.phase != VoicePhase.RECORDING) return@finish
        val recorded = SystemClock.elapsedRealtime() - startedAt.value
        when {
            recorded < MIN_RECORD_MILLIS -> { cancelAll(); voice.message = "说话时间太短" }
            else -> {
                voice.phase = VoicePhase.RECOGNIZING
                voice.level = 0f
                val early = earlyText.value
                when {
                    early != null -> deliver(early)
                    engine.value == VoiceEngine.CLOUD -> finishCloud()
                    recognizer.value != null -> recognizer.value?.stopListening()
                    else -> { reset(); voice.message = "没有听清，请再说一次" }
                }
            }
        }
    }

    voice.onCancel = { cancelAll() }
    voice.onClose = { cancelAll(); voice.voiceMode = false }

    // Both engines share the same bounded capture and measured waveform.
    LaunchedEffect(voice.phase) {
        while (voice.phase == VoicePhase.RECORDING) {
            recorder.value?.let { voice.level = it.level }
            voice.wave.removeAt(0)
            voice.wave.add(voice.level)
            voice.elapsedSeconds = ((SystemClock.elapsedRealtime() - startedAt.value) / 1000).toInt()
            if (voice.elapsedSeconds >= SPEECH_MAX_SECONDS) { voice.finish(); break }
            delay(70)
        }
    }
    LaunchedEffect(voice.phase) {
        if (voice.phase == VoicePhase.RECOGNIZING) {
            delay(35_000)
            if (voice.phase == VoicePhase.RECOGNIZING) {
                cancelAll()
                voice.message = "语音识别超时，请再试一次或输入文字"
            }
        }
    }
    DisposableEffect(lifecycle) {
        alive.value = true
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_STOP) cancelAll() }
        lifecycle.addObserver(observer)
        onDispose { alive.value = false; lifecycle.removeObserver(observer); cancelAll() }
    }
    LaunchedEffect(enabled) { if (!enabled) { cancelAll(); voice.voiceMode = false } }

    if (voice.askConsent) {
        AlertDialog(onDismissRequest = { voice.askConsent = false },
            title = { Text("使用云端语音识别") },
            text = { Text("本设备没有端侧语音识别。若继续，您说的这段话（最长 $SPEECH_MAX_SECONDS 秒）会经本机网关发送到阿里云百炼语音识别服务转成文字；录音不保存在手机上，识别结果只填入输入框，由您核对后再发送。转账等操作仍需在确认卡上确认。") },
            confirmButton = { TextButton(modifier = Modifier.testTag("voice_cloud_consent"), onClick = {
                voice.askConsent = false; prefs.edit { putBoolean(CLOUD_CONSENT_KEY, true) }; enterVoiceMode(VoiceEngine.CLOUD)
            }) { Text("同意并使用") } },
            dismissButton = { TextButton(onClick = { voice.askConsent = false }) { Text("不同意") } })
    }
    voice.message?.let { text ->
        AlertDialog(onDismissRequest = { voice.message = null },
            title = { Text("语音输入") }, text = { Text(text) },
            confirmButton = { TextButton(colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.onSurface), onClick = { voice.message = null }) { Text("知道了") } })
    }
    return voice
}
