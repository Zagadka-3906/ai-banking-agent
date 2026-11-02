package com.aibanking.agent.ui.chat

import androidx.activity.compose.BackHandler
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.awaitLongPressOrCancellation
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.components.*
import java.util.Locale

/** A bounded dictation panel: recording never sends a banking message. */
@Composable
@OptIn(ExperimentalLayoutApi::class)
internal fun VoiceInputPanel(voice: VoiceInput, modifier: Modifier = Modifier) {
    val colors = MaterialTheme.colorScheme
    val recording = voice.phase == VoicePhase.RECORDING
    val recognizing = voice.phase == VoicePhase.RECOGNIZING
    val haptics = LocalHapticFeedback.current
    val cancelDistance = with(LocalDensity.current) { 96.dp.toPx() }
    val panelLimit = with(LocalDensity.current) { (LocalWindowInfo.current.containerSize.height * .68f).toDp() }
    BackHandler(enabled = voice.voiceMode) { voice.close() }
    Column(modifier.fillMaxWidth().heightIn(max = panelLimit).verticalScroll(rememberScrollState())
        .testTag("voice_panel").padding(12.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Surface(shape = CircleShape, color = colors.secondaryContainer) {
                BankIcon(BankIconKind.Mic, Modifier.padding(10.dp).size(20.dp), tint = colors.onSecondaryContainer)
            }
            Column(Modifier.weight(1f).padding(start = 10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text("语音输入", style = MaterialTheme.typography.titleSmall)
                Text(if (voice.cloudRecognition) "云端转写" else "本机转写", style = MaterialTheme.typography.labelSmall,
                    color = colors.onSurfaceVariant)
            }
            BankTextButton(onClick = { voice.close() }, modifier = Modifier.testTag("voice_input")) {
                BankIcon(BankIconKind.Keyboard, Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text("键盘")
            }
        }
        Column(Modifier.fillMaxWidth().semantics { liveRegion = LiveRegionMode.Polite },
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(when (voice.phase) {
                VoicePhase.IDLE -> "说说你想做什么"
                VoicePhase.RECORDING -> if (voice.cancelArmed) "松开即可取消" else "正在听你说"
                VoicePhase.RECOGNIZING -> "正在转成文字"
            }, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center)
            Text(when (voice.phase) {
                VoicePhase.IDLE -> "例如：帮我查一下本月的消费"
                VoicePhase.RECORDING -> "松开转成文字，上滑取消 · 最长 ${SPEECH_MAX_SECONDS} 秒"
                VoicePhase.RECOGNIZING -> "请稍候，识别结果将填入输入框"
            }, style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant, textAlign = TextAlign.Center)
        }
        Surface(shape = RoundedCornerShape(20.dp), color = colors.surfaceContainerHighest) {
            Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
                horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (recognizing) {
                    Box(Modifier.fillMaxWidth().height(48.dp), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator(Modifier.size(30.dp).testTag("voice_transcribing"), color = colors.secondary, strokeWidth = 2.dp)
                    }
                } else VoiceWaveform(voice.wave.toList(), recording, Modifier.fillMaxWidth().height(48.dp))
                Text(voiceElapsedLabel(voice.elapsedSeconds), Modifier.testTag("voice_timer"),
                    style = MaterialTheme.typography.labelLarge, color = colors.onSurfaceVariant)
            }
        }
        if (recording && voice.partialText.isNotBlank()) {
            Text(voice.partialText, Modifier.fillMaxWidth().testTag("voice_partial"), maxLines = 2, overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.bodyMedium, color = colors.onSurfaceVariant, textAlign = TextAlign.Center)
        }
        FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (recording || recognizing) BankSecondaryButton("取消", onClick = { voice.cancel() },
                modifier = Modifier.heightIn(min = 52.dp).testTag("voice_cancel"))
            Surface(
                modifier = Modifier.weight(1f).heightIn(min = 56.dp).testTag(if (recording) "voice_finish" else "voice_start")
                    .semantics {
                        role = Role.Button
                        if (recognizing) disabled()
                        contentDescription = if (recognizing) "正在转写" else "长按说话，松开转文字，上滑取消"
                        if (!recognizing) onClick(label = if (recording) "完成转文字" else "开始录音") {
                            if (voice.phase == VoicePhase.RECORDING) voice.finish() else voice.start()
                            true
                        }
                    }
                    .pointerInput(voice) {
                        awaitEachGesture {
                            val down = awaitFirstDown()
                            if (voice.phase != VoicePhase.IDLE) return@awaitEachGesture
                            val longPress = awaitLongPressOrCancellation(down.id) ?: return@awaitEachGesture
                            haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                            voice.start()
                            var released = false
                            try {
                                while (true) {
                                    val event = awaitPointerEvent()
                                    val change = event.changes.firstOrNull { it.id == longPress.id } ?: break
                                    val cancel = change.position.y - down.position.y < -cancelDistance
                                    if (voice.phase == VoicePhase.RECORDING && voice.cancelArmed != cancel) {
                                        voice.cancelArmed = cancel
                                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                    }
                                    change.consume()
                                    if (!change.pressed) {
                                        released = true
                                        voice.finish()
                                        break
                                    }
                                }
                            } finally {
                                if (!released && voice.phase == VoicePhase.RECORDING) voice.cancel()
                            }
                        }
                    },
                shape = RoundedCornerShape(18.dp),
                color = when {
                    voice.cancelArmed -> colors.errorContainer
                    recognizing -> colors.surfaceContainerHighest
                    else -> colors.primary
                },
                contentColor = when {
                    voice.cancelArmed -> colors.onErrorContainer
                    recognizing -> colors.onSurfaceVariant
                    else -> colors.onPrimary
                },
            ) {
                Row(Modifier.padding(horizontal = 16.dp, vertical = 16.dp), horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically) {
                    if (!recognizing) {
                        BankIcon(if (voice.cancelArmed) BankIconKind.Close else BankIconKind.Mic, Modifier.size(20.dp))
                        Spacer(Modifier.width(8.dp))
                    }
                    Text(when {
                        voice.cancelArmed -> "松开取消"
                        recording -> "松开转文字"
                        recognizing -> "转写中…"
                        else -> "长按说话"
                    }, textAlign = TextAlign.Center)
                }
            }
        }
        Text("转成文字后，由你核对发送", Modifier.fillMaxWidth(), textAlign = TextAlign.Center,
            style = MaterialTheme.typography.labelSmall, color = colors.onSurfaceVariant)
    }
}

/** Measured amplitude only: silence stays flat and reduced motion updates directly. */
@Composable
private fun VoiceWaveform(levels: List<Float>, active: Boolean, modifier: Modifier = Modifier) {
    val motion = LocalBankMotionEnabled.current
    val colors = MaterialTheme.colorScheme
    val tint by animateColorAsState(if (active) colors.secondary else colors.outline,
        if (motion) tween(120) else snap(), label = "voiceWaveTint")
    val amplitudes = levels.map { level ->
        val height by animateFloatAsState(if (active) level.takeIf { it.isFinite() }?.coerceIn(0f, 1f) ?: 0f else 0f,
            if (motion) tween(80) else snap(), label = "voiceAmplitude")
        height
    }
    Canvas(modifier.testTag("voice_waveform").semantics { contentDescription = if (active) "麦克风实时音量" else "等待开始录音" }) {
        val step = size.width / amplitudes.size.coerceAtLeast(1)
        val width = minOf(4.dp.toPx(), step * .55f)
        amplitudes.forEachIndexed { index, level ->
            val height = 4.dp.toPx() + (size.height - 4.dp.toPx()).coerceAtLeast(0f) * level
            drawRoundRect(tint, Offset(step * (index + .5f) - width / 2, (size.height - height) / 2),
                Size(width, height), CornerRadius(width / 2))
        }
    }
}

internal fun voiceElapsedLabel(seconds: Int): String {
    val elapsed = seconds.coerceIn(0, SPEECH_MAX_SECONDS)
    return String.format(Locale.ROOT, "%02d:%02d / 00:%02d", elapsed / 60, elapsed % 60, SPEECH_MAX_SECONDS)
}

@Composable
internal fun VoiceModeToggle(voice: VoiceInput, enabled: Boolean) {
    IconButton(onClick = { voice.toggle() }, enabled = enabled,
        modifier = Modifier.testTag("voice_input").semantics { contentDescription = "语音输入" }) {
        BankIcon(BankIconKind.Mic, tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
