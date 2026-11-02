package com.aibanking.agent.ui.chat

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

internal const val SPEECH_SAMPLE_RATE = 16_000
internal const val SPEECH_MAX_SECONDS = 20

/** Wrap 16-bit mono PCM in the minimal WAV header the gateway accepts. */
internal fun pcmToWav(pcm: ByteArray, sampleRate: Int = SPEECH_SAMPLE_RATE): ByteArray {
    val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN).apply {
        put("RIFF".toByteArray()); putInt(36 + pcm.size); put("WAVE".toByteArray())
        put("fmt ".toByteArray()); putInt(16); putShort(1); putShort(1)
        putInt(sampleRate); putInt(sampleRate * 2); putShort(2); putShort(16)
        put("data".toByteArray()); putInt(pcm.size)
    }
    return header.array() + pcm
}

/**
 * Records one short utterance for cloud recognition.
 *
 * Audio stays in memory only (never written to storage) and is capped at
 * [SPEECH_MAX_SECONDS]; [onLimit] fires when the cap stops the recording.
 */
internal interface VoiceAudioCapture {
    val level: Float
    fun start(): Boolean
    fun stop(): ByteArray?
    fun cancel()
}

internal class WavRecorder(private val onLimit: () -> Unit) : VoiceAudioCapture {
    private var record: AudioRecord? = null
    private var worker: Thread? = null
    private val pcm = ByteArrayOutputStream()
    @Volatile private var running = false
    /** Loudness of the latest buffer, 0..1, for the recording waveform. */
    @Volatile override var level = 0f
        private set

    /** Caller must hold RECORD_AUDIO (requested by the voice button just before). */
    @SuppressLint("MissingPermission")
    override fun start(): Boolean {
        val minimum = AudioRecord.getMinBufferSize(SPEECH_SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minimum <= 0) return false
        val recorder = runCatching {
            AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, SPEECH_SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, minimum * 2)
        }.getOrNull() ?: return false
        if (recorder.state != AudioRecord.STATE_INITIALIZED) { recorder.release(); return false }
        record = recorder
        pcm.reset()
        running = true
        try {
            recorder.startRecording()
        } catch (_: Exception) {
            running = false
            recorder.release()
            record = null
            return false
        }
        val limitBytes = SPEECH_SAMPLE_RATE * 2 * SPEECH_MAX_SECONDS
        worker = Thread({
            val buffer = ByteArray(minimum)
            while (running) {
                val read = recorder.read(buffer, 0, buffer.size)
                if (read > 0) {
                    synchronized(pcm) { pcm.write(buffer, 0, minOf(read, limitBytes - pcm.size())) }
                    level = pcmLevel(buffer, read)
                }
                if (pcm.size() >= limitBytes) { running = false; onLimit() }
            }
        }, "speech-recorder").also { it.start() }
        return true
    }

    /** Stop and return the clip as WAV, or null when nothing was captured. */
    override fun stop(): ByteArray? {
        running = false
        worker?.join(1_000)
        worker = null
        record?.run { runCatching { stop() }; release() }
        record = null
        val captured = synchronized(pcm) { pcm.toByteArray().also { pcm.reset() } }
        return captured.takeIf { it.isNotEmpty() }?.let(::pcmToWav)
    }

    /** Discard without returning audio (screen left, voice disabled). */
    override fun cancel() { stop() }
}

/** Perceptual meter for quiet phone microphones; true silence remains zero. */
internal fun pcmLevel(buffer: ByteArray, length: Int): Float {
    var sum = 0.0
    var samples = 0
    var index = 0
    while (index + 1 < length) {
        val sample = (buffer[index].toInt() and 0xFF) or (buffer[index + 1].toInt() shl 8)
        sum += sample.toDouble() * sample
        samples++
        index += 2
    }
    if (samples == 0) return 0f
    val rms = kotlin.math.sqrt(sum / samples) / 32768.0
    if (rms <= 0.0001) return 0f
    val decibels = 20 * kotlin.math.log10(rms)
    return ((decibels + 70) / 58).coerceIn(0.0, 1.0).toFloat()
}
