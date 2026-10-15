package com.aibanking.agent

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.os.Looper
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.aibanking.agent.ui.chat.extractCardNumber
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import java.util.concurrent.TimeUnit
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test
import org.junit.runner.RunWith

/** Exercises the bundled OCR native libraries, including on the API 37 16 KB emulator. */
@RunWith(AndroidJUnit4::class)
@BankingRegression
class OcrRuntimeCompatibilityTest {
    @Test fun bundled_latin_ocr_reads_a_card_number_and_passes_the_actual_card_parser() {
        assertNotEquals("OCR must not block the main thread", Looper.getMainLooper(), Looper.myLooper())
        val bitmap = Bitmap.createBitmap(1400, 240, Bitmap.Config.ARGB_8888)
        try {
            Canvas(bitmap).apply {
                drawColor(Color.WHITE)
                drawText(CARD_NUMBER, 48f, 150f, Paint(Paint.ANTI_ALIAS_FLAG).apply {
                    color = Color.BLACK
                    textSize = 96f
                    typeface = Typeface.MONOSPACE
                })
            }
            val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
            try {
                val result = Tasks.await(recognizer.process(InputImage.fromBitmap(bitmap, 0)), 45, TimeUnit.SECONDS)
                assertEquals("Actual OCR output: ${result.text}", CARD_NUMBER, extractCardNumber(result.text))
            } finally {
                recognizer.close()
            }
        } finally {
            bitmap.recycle()
        }
    }

    private companion object {
        const val CARD_NUMBER = "6222020000023082"
    }
}
