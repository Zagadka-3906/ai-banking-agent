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
internal class WavRecorder(private val onLimit: () -> Unit) {
    private var record: AudioRecord? = null
    private var worker: Thread? = null
    private val pcm = ByteArrayOutputStream()
    @Volatile private var running = false

    /** Caller must hold RECORD_AUDIO (requested by the voice button just before). */
    @SuppressLint("MissingPermission")
    fun start(): Boolean {
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
        recorder.startRecording()
        val limitBytes = SPEECH_SAMPLE_RATE * 2 * SPEECH_MAX_SECONDS
        worker = Thread({
            val buffer = ByteArray(minimum)
            while (running) {
                val read = recorder.read(buffer, 0, buffer.size)
                if (read > 0) synchronized(pcm) { pcm.write(buffer, 0, minOf(read, limitBytes - pcm.size())) }
                if (pcm.size() >= limitBytes) { running = false; onLimit() }
            }
        }, "speech-recorder").also { it.start() }
        return true
    }

    /** Stop and return the clip as WAV, or null when nothing was captured. */
    fun stop(): ByteArray? {
        running = false
        worker?.join(1_000)
        worker = null
        record?.run { runCatching { stop() }; release() }
        record = null
        val captured = synchronized(pcm) { pcm.toByteArray().also { pcm.reset() } }
        return captured.takeIf { it.isNotEmpty() }?.let(::pcmToWav)
    }

    /** Discard without returning audio (screen left, voice disabled). */
    fun cancel() { stop() }
}
