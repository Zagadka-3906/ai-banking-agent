package com.aibanking.agent.ui.anim

import android.graphics.BlurMaskFilter
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PathMeasure
import android.graphics.RectF
import android.graphics.SweepGradient
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** The three risk tiers a server turn can be assigned. */
enum class RiskTier { GREEN, YELLOW, RED }

/** Representative colour of each tier (legend, tests). */
fun RiskTier.glowColor(): Color = when (this) {
    RiskTier.GREEN -> Color(0xFF34A853)
    RiskTier.YELLOW -> Color(0xFFFBBC04)
    RiskTier.RED -> Color(0xFFEA4335)
}

/**
 * Several neighbouring hues per tier, spread around the border by the sweep
 * gradient so the light reads as a soft halo rather than a flat outline.
 */
private fun RiskTier.glowPalette(): IntArray = when (this) {
    RiskTier.GREEN -> longArrayOf(0xFF34A853, 0xFF81C995, 0xFF00BFA5, 0xFFA8DAB5, 0xFF34A853)
    RiskTier.YELLOW -> longArrayOf(0xFFFBBC04, 0xFFFFD54F, 0xFFFF9800, 0xFFFFE082, 0xFFFFB300, 0xFFFBBC04)
    RiskTier.RED -> longArrayOf(0xFFEA4335, 0xFFFF6E6E, 0xFFFF7043, 0xFFF06292, 0xFFD50000, 0xFFEA4335)
}.map { it.toInt() }.toIntArray()

fun riskTierOf(level: String?): RiskTier? = when (level?.uppercase()) {
    "GREEN" -> RiskTier.GREEN
    "YELLOW" -> RiskTier.YELLOW
    "RED" -> RiskTier.RED
    else -> null
}

/**
 * One risk determination to show at the screen edge. [key] identifies the event
 * (a reply or an approval) so the same event never lights up twice; [sustained]
 * keeps the light on while the user still has to decide.
 */
@Immutable
data class RiskSignal(val key: String, val tier: RiskTier, val sustained: Boolean)

/** A blurred stroke layer of the glow: width, blur radius and opacity, in dp / 0..1. */
private class GlowLayer(val strokeDp: Float, val blurDp: Float, val alpha: Float) {
    val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    var blurPx = -1f
}

/**
 * A soft, living light around the screen in the colour of the risk tier, in the
 * manner of the Gemini input glow: a gradient of neighbouring hues along the
 * border, drawn as three blurred strokes (wide bloom, middle glow, bright core).
 * The light breathes and smoothly flows along the border. It never pops in - the ring
 * grows from the middle of the screen out to the edges while it fades up - flows,
 * breathes, and fades out when done. A waiting approval keeps it flowing and breathing.
 *
 * Drawing only; it never takes touches. With system animations off the light
 * simply appears at the edges and disappears.
 */
fun Modifier.riskEdgeGlow(signal: RiskSignal?): Modifier = composed {
    val motion = LocalBankMotionEnabled.current
    val intensity = remember { Animatable(0f) }
    val spread = remember { Animatable(1f) }
    val flow = remember { Animatable(0f) }
    val flowMatrix = remember { Matrix() }
    // Events already on screen when composed (a restored conversation) or already
    // shown once never light up again.
    val shown = remember { mutableSetOf<String>().apply { signal?.key?.let(::add) } }
    LaunchedEffect(signal) {
        val fresh = signal != null && (signal.sustained || shown.add(signal.key))
        if (signal == null || !fresh) {
            if (motion) intensity.animateTo(0f, tween(800, easing = FastOutSlowInEasing)) else intensity.snapTo(0f)
            return@LaunchedEffect
        }
        coroutineScope {
            val flowJob = if (motion) {
                spread.snapTo(0f)
                intensity.snapTo(0f)
                // Light rises from the middle of both sides and runs up and down the
                // border until the four ends meet at the top and the bottom.
                launch { spread.animateTo(1f, tween(1300, easing = FastOutSlowInEasing)) }
                launch {
                    flow.snapTo(0f)
                    flow.animateTo(360f, infiniteRepeatable(tween(5000, easing = LinearEasing), RepeatMode.Restart))
                }
            } else {
                spread.snapTo(1f)
                intensity.snapTo(1f)
                flow.snapTo(0f)
                null
            }
            if (motion) {
                intensity.animateTo(1f, tween(500, easing = FastOutSlowInEasing))
            }
            if (signal.sustained) {
                // An infinite spec (not a loop) so UI tests can still reach idle.
                if (motion) intensity.animateTo(0.55f, infiniteRepeatable(tween(1500, easing = FastOutSlowInEasing), RepeatMode.Reverse))
            } else if (motion) {
                // Two slow breaths, then the light fades away.
                repeat(2) {
                    intensity.animateTo(0.55f, tween(1100, easing = FastOutSlowInEasing))
                    intensity.animateTo(1f, tween(1100, easing = FastOutSlowInEasing))
                }
                intensity.animateTo(0f, tween(1200, easing = FastOutSlowInEasing))
                flowJob?.cancel()
            } else {
                delay(2600L)
                intensity.snapTo(0f)
            }
        }
    }
    val layers = remember {
        listOf(
            // Sized for real phones: the stroke is centred on the screen edge, so
            // only half of each layer is visible, and bright surroundings wash out
            // a faint light. Wider layers reach further into the screen.
            GlowLayer(strokeDp = 64f, blurDp = 52f, alpha = 0.72f), // wide bloom
            GlowLayer(strokeDp = 28f, blurDp = 20f, alpha = 0.95f), // glow
            GlowLayer(strokeDp = 6f, blurDp = 4f, alpha = 1.00f), // bright core
        )
    }
    val border = remember { BorderPath() }
    // Keep the last tier so the light can fade out after the approval is resolved.
    val lastTier = remember { arrayOfNulls<RiskTier>(1) }
    signal?.tier?.let { lastTier[0] = it }
    drawWithContent {
        drawContent()
        val a = intensity.value
        val tier = lastTier[0]
        if (a <= 0.01f || tier == null) return@drawWithContent
        val cx = size.width / 2f
        val cy = size.height / 2f
        val shader = SweepGradient(cx, cy, tier.glowPalette(), null)
        flowMatrix.setRotate(flow.value, cx, cy)
        shader.setLocalMatrix(flowMatrix)
        val path = border.revealed(size.width, size.height, 40.dp.toPx(), spread.value)
        drawIntoCanvas { canvas ->
            layers.forEach { layer ->
                val blur = layer.blurDp.dp.toPx()
                if (layer.blurPx != blur) {
                    layer.paint.maskFilter = BlurMaskFilter(blur, BlurMaskFilter.Blur.NORMAL)
                    layer.blurPx = blur
                }
                layer.paint.strokeWidth = layer.strokeDp.dp.toPx()
                layer.paint.strokeCap = Paint.Cap.ROUND
                layer.paint.shader = shader
                layer.paint.alpha = (255 * a * layer.alpha).toInt().coerceIn(0, 255)
                // The stroke is centred on the screen edge, so half the light falls inside.
                canvas.nativeCanvas.drawPath(path, layer.paint)
            }
        }
    }
}

/**
 * The screen border as one closed path that starts at the top centre and runs
 * clockwise, so by symmetry the right middle sits at a quarter of its length,
 * the bottom centre at half and the left middle at three quarters. Revealing
 * a growing window around both side midpoints makes the light run up and down
 * from the sides and meet at the top and bottom centres.
 */
private class BorderPath {
    private val full = Path()
    private val revealed = Path()
    private val measure = PathMeasure()
    private var key = 0L

    fun revealed(width: Float, height: Float, corner: Float, progress: Float): Path {
        val sizeKey = (width.toBits().toLong() shl 32) or height.toBits().toLong()
        if (sizeKey != key) {
            key = sizeKey
            val r = corner.coerceAtMost(minOf(width, height) / 2f)
            full.reset()
            full.moveTo(width / 2f, 0f)
            full.lineTo(width - r, 0f)
            full.arcTo(RectF(width - 2 * r, 0f, width, 2 * r), 270f, 90f)
            full.lineTo(width, height - r)
            full.arcTo(RectF(width - 2 * r, height - 2 * r, width, height), 0f, 90f)
            full.lineTo(r, height)
            full.arcTo(RectF(0f, height - 2 * r, 2 * r, height), 90f, 90f)
            full.lineTo(0f, r)
            full.arcTo(RectF(0f, 0f, 2 * r, 2 * r), 180f, 90f)
            full.close()
            measure.setPath(full, false)
        }
        revealed.rewind()
        val length = measure.length
        val half = length / 4f * progress.coerceIn(0f, 1f)
        if (half <= 0f) return revealed
        for (center in floatArrayOf(length / 4f, length * 3f / 4f)) {
            measure.getSegment(center - half, center + half, revealed, true)
        }
        return revealed
    }
}
