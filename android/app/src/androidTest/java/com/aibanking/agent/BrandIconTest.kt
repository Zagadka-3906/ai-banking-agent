package com.aibanking.agent

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.drawable.AdaptiveIconDrawable
import android.graphics.drawable.Drawable
import android.os.Build
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Native resource rendering: launcher padding, real cutouts and themed-icon masks. */
class BrandIconTest {
    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun foreground_keeps_transparent_padding_and_both_open_lines() {
        val bitmap = render(drawable(R.drawable.ic_launcher_foreground), 540)
        try {
            val bounds = visibleBounds(bitmap)
            // The launcher canvas is 108 units; the safe square is [21, 87].
            assertTrue("brand must stay inside the adaptive icon safe area", bounds.left >= 105 && bounds.top >= 105)
            assertTrue("brand must stay inside the adaptive icon safe area", bounds.right <= 435 && bounds.bottom <= 435)
            assertTrue("brand must remain readable instead of shrinking to a tiny logo", bounds.width() >= 240 && bounds.height() >= 240)
            for (y in bounds.top until bounds.bottom) for (x in bounds.left until bounds.right) {
                if (Color.alpha(bitmap.getPixel(x, y)) > 0) {
                    val dx = x + 0.5 - 270
                    val dy = y + 0.5 - 270
                    assertTrue("every visible pixel must also fit the 66-unit circular safe area",
                        dx * dx + dy * dy <= 165.0 * 165.0)
                }
            }
            assertEquals(0, Color.alpha(bitmap.getPixel(0, 0)))
            assertEquals(0, Color.alpha(bitmap.getPixel(539, 539)))
            assertEquals("the first line must be a transparent cutout", 0, Color.alpha(bitmap.getPixel(270, 230)))
            assertEquals("the second line must be a transparent cutout", 0, Color.alpha(bitmap.getPixel(270, 300)))
            assertEquals("the shield body must be solid", 255, Color.alpha(bitmap.getPixel(270, 175)))
        } finally {
            bitmap.recycle()
        }
    }

    @Test fun launcher_has_an_opaque_brand_background_and_a_matching_monochrome_silhouette() {
        val launcher = drawable(R.mipmap.ic_launcher)
        assertTrue("API 26+ uses an adaptive icon", launcher is AdaptiveIconDrawable)
        launcher as AdaptiveIconDrawable
        val background = render(launcher.background, 108)
        val foreground = render(launcher.foreground, 108)
        try {
            val purple = context.getColor(R.color.ic_launcher_background)
            assertEquals(255, Color.alpha(purple))
            for (y in 0 until background.height) for (x in 0 until background.width) {
                assertEquals("the icon background must not blend with launcher wallpaper", purple, background.getPixel(x, y))
            }
            if (Build.VERSION.SDK_INT >= 33) {
                val monochrome = checkNotNull(launcher.monochrome) { "Android 13+ must expose a monochrome layer" }
                val mask = render(monochrome, 108)
                try {
                    for (y in 0 until mask.height) for (x in 0 until mask.width) {
                        assertEquals("themed and full-color icons must share the same silhouette",
                            Color.alpha(foreground.getPixel(x, y)), Color.alpha(mask.getPixel(x, y)))
                    }
                    assertEquals(0, Color.alpha(mask.getPixel(54, 46)))
                    assertEquals(0, Color.alpha(mask.getPixel(54, 60)))
                } finally {
                    mask.recycle()
                }
            }
        } finally {
            foreground.recycle()
            background.recycle()
        }
    }

    @Test fun common_launcher_masks_preserve_the_mark_and_export_visual_comparisons() {
        val source = render(drawable(R.drawable.ic_launcher_foreground), 432)
        val board = Bitmap.createBitmap(756, 840, Bitmap.Config.ARGB_8888)
        try {
            val canvas = Canvas(board)
            canvas.drawColor(Color.WHITE)
            val label = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = INK; textSize = 18f }
            canvas.drawText("ANYAN / adaptive icon masks / large + 48px", 20f, 26f, label)
            val variants = listOf(
                Triple("Brand", context.getColor(R.color.ic_launcher_background), INK),
                Triple("Themed light", Color.rgb(234, 229, 252), Color.rgb(61, 49, 90)),
                Triple("Themed dark", Color.rgb(61, 49, 90), Color.rgb(234, 229, 252)),
            )
            variants.forEachIndexed { row, (name, background, foreground) ->
                masks.forEachIndexed { column, shape ->
                    val glyph = viewport(source, 192)
                    val clipped = clip(glyph, shape)
                    try {
                        assertTrue("$shape must not cut the brand mark",
                            alphaSum(clipped) >= alphaSum(glyph) * 0.999)
                    } finally {
                        glyph.recycle()
                        clipped.recycle()
                    }
                    val large = coloredIcon(source, shape, 192, background, foreground)
                    val small = coloredIcon(source, shape, 48, background, foreground)
                    try {
                        val left = 30f + column * 252f
                        val top = 48f + row * 264f
                        canvas.drawBitmap(large, left, top, null)
                        canvas.drawBitmap(small, left + 160f, top + 200f, null)
                        canvas.drawText(name, left, top + 216f, label)
                        canvas.drawText(shape, left, top + 240f, label)
                    } finally {
                        large.recycle()
                        small.recycle()
                    }
                }
            }
            save(board, "brand-icon-masks.png")
            val actualLauncher = render(drawable(R.mipmap.ic_launcher), 192)
            try {
                save(actualLauncher, "brand-icon-device-mask.png")
            } finally {
                actualLauncher.recycle()
            }
        } finally {
            source.recycle()
            board.recycle()
        }
    }

    private fun drawable(id: Int): Drawable = checkNotNull(context.getDrawable(id)).mutate()

    private fun render(drawable: Drawable, size: Int): Bitmap {
        val result = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        drawable.setBounds(0, 0, size, size)
        drawable.draw(Canvas(result))
        return result
    }

    private fun visibleBounds(bitmap: Bitmap): Rect {
        var left = bitmap.width
        var top = bitmap.height
        var right = 0
        var bottom = 0
        for (y in 0 until bitmap.height) for (x in 0 until bitmap.width) {
            if (Color.alpha(bitmap.getPixel(x, y)) > 0) {
                left = minOf(left, x)
                top = minOf(top, y)
                right = maxOf(right, x + 1)
                bottom = maxOf(bottom, y + 1)
            }
        }
        assertTrue("the mark must contain visible pixels", right > left && bottom > top)
        return Rect(left, top, right, bottom)
    }

    /** Adaptive icons expose the central 72 units of a 108-unit layer. */
    private fun viewport(source: Bitmap, size: Int): Bitmap {
        val result = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val margin = source.width / 6
        Canvas(result).drawBitmap(source, Rect(margin, margin, source.width - margin, source.height - margin),
            Rect(0, 0, size, size), Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG))
        return result
    }

    private fun mask(shape: String, size: Int): Path = Path().apply {
        val edge = size.toFloat()
        val box = RectF(0f, 0f, edge, edge)
        when (shape) {
            "circle" -> addCircle(edge / 2, edge / 2, edge / 2, Path.Direction.CW)
            "rounded" -> addRoundRect(box, edge * 0.24f, edge * 0.24f, Path.Direction.CW)
            "teardrop" -> addRoundRect(box, floatArrayOf(edge / 2, edge / 2, edge / 2, edge / 2,
                0f, 0f, edge / 2, edge / 2), Path.Direction.CW)
            else -> error("Unknown mask")
        }
    }

    private fun clip(source: Bitmap, shape: String): Bitmap {
        val result = Bitmap.createBitmap(source.width, source.height, Bitmap.Config.ARGB_8888)
        Canvas(result).apply {
            clipPath(mask(shape, source.width))
            drawBitmap(source, 0f, 0f, null)
        }
        return result
    }

    private fun coloredIcon(source: Bitmap, shape: String, size: Int, background: Int, foreground: Int): Bitmap {
        val glyph = viewport(source, size)
        val result = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        try {
            val canvas = Canvas(result)
            canvas.clipPath(mask(shape, size))
            canvas.drawColor(background)
            val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                colorFilter = android.graphics.PorterDuffColorFilter(foreground, android.graphics.PorterDuff.Mode.SRC_IN)
            }
            canvas.drawBitmap(glyph, 0f, 0f, paint)
        } finally {
            glyph.recycle()
        }
        return result
    }

    private fun alphaSum(bitmap: Bitmap): Long {
        var total = 0L
        for (y in 0 until bitmap.height) for (x in 0 until bitmap.width) total += Color.alpha(bitmap.getPixel(x, y))
        return total
    }

    private fun save(bitmap: Bitmap, name: String) {
        val output = InstrumentationRegistry.getArguments().getString("additionalTestOutputDir")
        val folder = if (output != null) File(output, "brand-review")
            else File(context.getExternalFilesDir(null), "brand-review")
        check(folder.isDirectory || folder.mkdirs())
        File(folder, name).outputStream().use { check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) }
    }

    private companion object {
        val INK = Color.rgb(22, 19, 31)
        val masks = listOf("circle", "rounded", "teardrop")
    }
}
