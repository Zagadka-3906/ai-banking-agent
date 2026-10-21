package com.aibanking.agent

import com.aibanking.agent.ui.chat.SPEECH_SAMPLE_RATE
import com.aibanking.agent.ui.chat.pcmLevel
import com.aibanking.agent.ui.chat.pcmToWav
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * The gateway accepts exactly 16 kHz mono 16-bit PCM WAV; pin the header we send,
 * and the loudness meter the recording waveform is drawn from.
 */
class WavRecorderTest {
    @Test
    fun wavHeaderMatchesTheGatewayContract() {
        val pcm = ByteArray(SPEECH_SAMPLE_RATE * 2) { (it % 7).toByte() }  // one second
        val wav = pcmToWav(pcm)
        val header = ByteBuffer.wrap(wav, 0, 44).order(ByteOrder.LITTLE_ENDIAN)
        assertEquals("RIFF", String(wav, 0, 4))
        assertEquals(36 + pcm.size, header.getInt(4))
        assertEquals("WAVEfmt ", String(wav, 8, 8))
        assertEquals(16, header.getInt(16))
        assertEquals(1, header.getShort(20).toInt())          // PCM
        assertEquals(1, header.getShort(22).toInt())          // mono
        assertEquals(16_000, header.getInt(24))
        assertEquals(32_000, header.getInt(28))               // byte rate
        assertEquals(2, header.getShort(32).toInt())          // block align
        assertEquals(16, header.getShort(34).toInt())         // bits per sample
        assertEquals("data", String(wav, 36, 4))
        assertEquals(pcm.size, header.getInt(40))
        assertArrayEquals(pcm, wav.copyOfRange(44, wav.size))
    }

    @Test
    fun silenceReadsZero() {
        val quiet = ByteArray(640)
        assertEquals(0f, pcmLevel(quiet, quiet.size), 0f)
        assertEquals(0f, pcmLevel(ByteArray(0), 0), 0f)
    }

    @Test
    fun aQuietSignalScalesLinearlyUnderTheSpeechGain() {
        // 1638 is 5% of full scale; the 6x gain that makes speech fill the meter puts it at 0.3.
        val pcm = littleEndian(1638, samples = 100)
        assertEquals(0.3f, pcmLevel(pcm, pcm.size), 1e-3f)
    }

    @Test
    fun aLoudSignalSaturatesAtOneInsteadOfOverflowing() {
        val pcm = littleEndian(16384, samples = 100)   // half full scale: 3.0 before the clamp
        assertEquals(1f, pcmLevel(pcm, pcm.size), 0f)
    }

    @Test
    fun negativeSamplesReadAsLoudAsTheirPositiveMirror() {
        val positive = littleEndian(1638, samples = 50)
        val negative = littleEndian(-1638, samples = 50)
        assertEquals(pcmLevel(positive, positive.size), pcmLevel(negative, negative.size), 1e-6f)
    }

    @Test
    fun aTrailingOddByteIsIgnoredRatherThanReadAsASample() {
        val pcm = littleEndian(1638, samples = 4).copyOf(9).also { it[8] = 0x7F }
        assertEquals(0.3f, pcmLevel(pcm, 9), 1e-3f)
    }

    /** [count] samples of a constant value, little-endian, as the recorder reads it. */
    private fun littleEndian(value: Int, samples: Int) = ByteArray(samples * 2) { index ->
        if (index % 2 == 0) (value and 0xFF).toByte() else ((value shr 8) and 0xFF).toByte()
    }
}
