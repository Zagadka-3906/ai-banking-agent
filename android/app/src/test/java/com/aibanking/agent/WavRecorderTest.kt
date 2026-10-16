package com.aibanking.agent

import com.aibanking.agent.ui.chat.SPEECH_SAMPLE_RATE
import com.aibanking.agent.ui.chat.pcmToWav
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** The gateway accepts exactly 16 kHz mono 16-bit PCM WAV; pin the header we send. */
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
}
