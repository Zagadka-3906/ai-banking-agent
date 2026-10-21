package com.aibanking.agent.ui.chat

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupProperties
import androidx.core.content.ContextCompat
import androidx.core.content.edit
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.aibanking.agent.ui.components.BankIcon
import com.aibanking.agent.ui.components.BankIconKind
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val VOICE_PREFS = "voice_input"
private const val CLOUD_CONSENT_KEY = "cloud_asr_consent"
private const val MIN_HOLD_MILLIS = 600L
private const val WAVE_BARS = 22

// The familiar chat-app palette for the recording bubble.
private val BubbleGreen = Color(0xFF95EC69)
private val BubbleRed = Color(0xFFFA5151)
private val WaveInk = Color(0xFF1B1B1B)

private enum class VoiceEngine { DEVICE, CLOUD }

internal enum class VoicePhase { IDLE, RECORDING, RECOGNIZING }

/**
 * Hold-to-talk voice input in the manner of a chat app: the composer turns into
 * a "按住 说话" bar; while held, a dimmed overlay shows a live waveform and the
 * elapsed time; sliding up cancels; releasing turns the speech into text in the
 * input box, where the user reviews it before sending. Nothing is sent by voice.
 *
 * On-device recognition is preferred: the audio never leaves the phone. Only
 * when the device has no recogniser, and the gateway has cloud recognition
 * configured, does the user get the option to upload a clip - after an
 * explicit, remembered consent that says where the audio goes.
 */
@Stable
internal class VoiceInput {
    var voiceMode by mutableStateOf(false)
    var phase by mutableStateOf(VoicePhase.IDLE)
    var cancelArmed by mutableStateOf(false)
    var elapsedSeconds by mutableIntStateOf(0)
    var level by mutableFloatStateOf(0f)
    val wave = mutableStateListOf<Float>().apply { repeat(WAVE_BARS) { add(0f) } }
    var message by mutableStateOf<String?>(null)
    var askConsent by mutableStateOf(false)

    internal var onToggle: () -> Unit = {}
    internal var onPress: () -> Unit = {}
    internal var onRelease: () -> Unit = {}

    fun toggle() = onToggle()
    fun press() = onPress()
    fun release() = onRelease()
}

@Composable
internal fun rememberVoiceInput(
    enabled: Boolean,
    captureResult: () -> (String) -> Unit,
    cloudAvailable: ((Boolean) -> Unit) -> Unit = { it(false) },
    transcribeCloud: (ByteArray, (String) -> Unit, (String) -> Unit) -> Unit = { _, _, fail -> fail("云端语音识别不可用") },
): VoiceInput {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val scope = rememberCoroutineScope()
    val prefs = remember { context.getSharedPreferences(VOICE_PREFS, Context.MODE_PRIVATE) }
    val voice = remember { VoiceInput() }
    val onDevice = remember { Build.VERSION.SDK_INT >= 31 && runCatching { SpeechRecognizer.isOnDeviceRecognitionAvailable(context) }.getOrDefault(false) }
    val engine = remember { mutableStateOf(VoiceEngine.DEVICE) }
    val recognizer = remember { mutableStateOf<SpeechRecognizer?>(null) }
    val recorder = remember { mutableStateOf<WavRecorder?>(null) }
    val pendingResult = remember { mutableStateOf<((String) -> Unit)?>(null) }
    val earlyText = remember { mutableStateOf<String?>(null) }
    val startedAt = remember { mutableStateOf(0L) }

    fun resumed() = lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)
    fun granted() = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    fun reset() {
        voice.phase = VoicePhase.IDLE
        voice.cancelArmed = false
        voice.level = 0f
        pendingResult.value = null
        earlyText.value = null
    }

    fun cancelAll() {
        val previous = recognizer.value; recognizer.value = null; previous?.cancel(); previous?.destroy()
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
                    // The recogniser may finish on silence while the user still holds the bar.
                    if (voice.phase == VoicePhase.RECORDING) earlyText.value = text else deliver(text)
                }
                override fun onPartialResults(partialResults: Bundle?) = Unit
                override fun onEvent(eventType: Int, params: Bundle?) = Unit
            })
            engineInstance.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
                putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            })
        } catch (_: Exception) { cancelAll(); voice.message = "本机语音服务不可用，请输入文字" }
    }

    fun startCloud() {
        val active = WavRecorder(onLimit = { scope.launch { voice.release() } })
        if (!active.start()) { cancelAll(); voice.message = "无法打开麦克风，请输入文字"; return }
        recorder.value = active
    }

    fun finishCloud() {
        val active = recorder.value ?: return
        recorder.value = null
        val wav = active.stop()
        if (wav == null) { reset(); voice.message = "没有录到声音，请再说一次"; return }
        transcribeCloud(wav, { text -> deliver(text) }, { failure -> reset(); voice.message = failure })
    }

    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) voice.voiceMode = true else voice.message = "未启用麦克风，可继续输入文字"
    }

    fun enterVoiceMode(useEngine: VoiceEngine) {
        engine.value = useEngine
        if (granted()) voice.voiceMode = true else permission.launch(Manifest.permission.RECORD_AUDIO)
    }

    voice.onToggle = {
        when {
            voice.voiceMode -> { cancelAll(); voice.voiceMode = false }
            !enabled -> Unit
            onDevice -> enterVoiceMode(VoiceEngine.DEVICE)
            else -> cloudAvailable { available ->
                when {
                    !available -> voice.message = "本设备不支持端侧语音识别，请输入文字"
                    prefs.getBoolean(CLOUD_CONSENT_KEY, false) -> enterVoiceMode(VoiceEngine.CLOUD)
                    else -> voice.askConsent = true
                }
            }
        }
    }
    voice.onPress = press@{
        if (!enabled || voice.phase != VoicePhase.IDLE) return@press
        if (!granted()) { permission.launch(Manifest.permission.RECORD_AUDIO); return@press }
        pendingResult.value = captureResult()
        earlyText.value = null
        voice.cancelArmed = false
        voice.elapsedSeconds = 0
        voice.wave.indices.forEach { voice.wave[it] = 0f }
        startedAt.value = System.currentTimeMillis()
        voice.phase = VoicePhase.RECORDING
        if (engine.value == VoiceEngine.CLOUD) startCloud() else startDevice()
    }
    voice.onRelease = release@{
        if (voice.phase != VoicePhase.RECORDING) return@release
        val held = System.currentTimeMillis() - startedAt.value
        when {
            voice.cancelArmed -> cancelAll()
            held < MIN_HOLD_MILLIS -> { cancelAll(); voice.message = "说话时间太短" }
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

    // Time and waveform while recording.
    LaunchedEffect(voice.phase) {
        while (voice.phase == VoicePhase.RECORDING) {
            recorder.value?.let { voice.level = it.level }
            voice.wave.removeAt(0)
            voice.wave.add(voice.level)
            voice.elapsedSeconds = ((System.currentTimeMillis() - startedAt.value) / 1000).toInt()
            delay(70)
        }
    }
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_STOP) cancelAll() }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer); recognizer.value?.destroy(); recognizer.value = null; recorder.value?.cancel(); recorder.value = null }
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
    if (voice.phase != VoicePhase.IDLE) VoiceOverlay(voice)
    return voice
}

/** Mic in text mode, keyboard in voice mode. */
@Composable
internal fun VoiceModeToggle(voice: VoiceInput, enabled: Boolean) {
    val label = if (voice.voiceMode) "切换到键盘输入" else "语音输入"
    IconButton(onClick = { voice.toggle() }, enabled = enabled || voice.voiceMode,
        modifier = Modifier.testTag("voice_input").semantics { contentDescription = label }) {
        BankIcon(if (voice.voiceMode) BankIconKind.Keyboard else BankIconKind.Mic, tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** The wide "按住 说话" bar that replaces the text field in voice mode. */
@Composable
internal fun HoldToTalkBar(voice: VoiceInput, modifier: Modifier = Modifier) {
    val haptics = LocalHapticFeedback.current
    val cancelDistance = with(LocalDensity.current) { 96.dp.toPx() }
    val held = voice.phase == VoicePhase.RECORDING
    val container by animateColorAsState(
        if (held) MaterialTheme.colorScheme.surfaceContainerHighest else MaterialTheme.colorScheme.surface, tween(120), label = "holdBar")
    Surface(
        shape = RoundedCornerShape(22.dp),
        color = container,
        modifier = modifier
            .heightIn(min = 48.dp)
            .padding(vertical = 4.dp)
            .testTag("voice_hold")
            .semantics { contentDescription = "按住说话" }
            .pointerInput(Unit) {
                awaitEachGesture {
                    val down = awaitFirstDown(requireUnconsumed = false)
                    haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                    voice.press()
                    while (true) {
                        val event = awaitPointerEvent()
                        val change = event.changes.firstOrNull { it.id == down.id } ?: break
                        val armed = change.position.y - down.position.y < -cancelDistance
                        if (armed != voice.cancelArmed) {
                            voice.cancelArmed = armed
                            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                        }
                        change.consume()
                        if (!change.pressed) break
                    }
                    voice.release()
                }
            },
    ) {
        Box(Modifier.fillMaxWidth().height(44.dp), contentAlignment = Alignment.Center) {
            Text(if (held) "松开 转文字" else "按住 说话", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
        }
    }
}

@Composable
private fun VoiceOverlay(voice: VoiceInput) {
    Popup(properties = PopupProperties(focusable = false, clippingEnabled = false)) {
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.55f))) {
            Column(Modifier.align(Alignment.Center), horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                val bubble by animateColorAsState(if (voice.cancelArmed) BubbleRed else BubbleGreen, tween(150), label = "bubble")
                Surface(shape = RoundedCornerShape(18.dp), color = bubble, shadowElevation = 6.dp) {
                    Box(Modifier.width(220.dp).height(84.dp), contentAlignment = Alignment.Center) {
                        if (voice.phase == VoicePhase.RECOGNIZING) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                CircularProgressIndicator(Modifier.size(20.dp), color = WaveInk, strokeWidth = 2.dp)
                                Text("识别中…", color = WaveInk, style = MaterialTheme.typography.titleSmall)
                            }
                        } else {
                            Waveform(voice.wave)
                        }
                    }
                }
                if (voice.phase == VoicePhase.RECORDING) {
                    Text(formatElapsed(voice.elapsedSeconds), color = Color.White.copy(alpha = 0.85f), style = MaterialTheme.typography.labelLarge)
                }
            }
            Column(Modifier.align(Alignment.BottomCenter).padding(bottom = 150.dp), horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(14.dp)) {
                if (voice.phase == VoicePhase.RECORDING) {
                    val size by animateDpAsState(if (voice.cancelArmed) 76.dp else 60.dp, tween(150), label = "cancelSize")
                    val disc by animateColorAsState(if (voice.cancelArmed) Color.White else Color.White.copy(alpha = 0.18f), tween(150), label = "cancelDisc")
                    Box(Modifier.size(size).clip(CircleShape).background(disc), contentAlignment = Alignment.Center) {
                        BankIcon(BankIconKind.Close, Modifier.size(26.dp), tint = if (voice.cancelArmed) BubbleRed else Color.White)
                    }
                }
                Text(
                    when {
                        voice.phase == VoicePhase.RECOGNIZING -> "正在把语音转成文字"
                        voice.cancelArmed -> "松开手指，取消输入"
                        else -> "松开 转文字，上滑 取消"
                    },
                    color = Color.White, style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
    }
}

@Composable
private fun Waveform(levels: List<Float>) {
    Row(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
        levels.forEach { level ->
            val height by animateDpAsState(4.dp + 30.dp * level, tween(90), label = "bar")
            Box(Modifier.width(3.dp).height(height).clip(RoundedCornerShape(2.dp)).background(WaveInk))
        }
        Spacer(Modifier.width(0.dp))
    }
}

private fun formatElapsed(seconds: Int): String {
    val left = SPEECH_MAX_SECONDS - seconds
    return if (left in 0..5) "还可以说 $left 秒" else "0:%02d".format(seconds)
}
