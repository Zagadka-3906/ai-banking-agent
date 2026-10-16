package com.aibanking.agent.ui.anim

import android.graphics.BlurMaskFilter
import android.graphics.Matrix
import android.graphics.Paint
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
 * Several neighbouring hues per tier: rotating them around the border is what
 * makes the light look alive rather than a flat outline (the Gemini look).
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
 * manner of the Gemini input glow: a sweep gradient of neighbouring hues slowly
 * rotates along the border and is drawn as three blurred strokes (wide bloom,
 * middle glow, bright core). It never pops in - the ring grows from the middle
 * of the screen out to the edges while it fades up - and fades out when done.
 * A waiting approval keeps it on and gently breathing.
 *
 * Drawing only; it never takes touches. With system animations off the light
 * simply appears at the edges and disappears.
 */
fun Modifier.riskEdgeGlow(signal: RiskSignal?): Modifier = composed {
    val motion = LocalBankMotionEnabled.current
    val intensity = remember { Animatable(0f) }
    val spread = remember { Animatable(1f) }
    // Events already on screen when composed (a restored conversation) or already
    // shown once never light up again.
    val rotation = remember { Animatable(0f) }
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
            // The hues travel around the border only while the light is visible.
            // An infinite spec (not a loop) so UI tests can still reach idle.
            val spin = if (motion) launch {
                rotation.snapTo(0f)
                rotation.animateTo(360f, infiniteRepeatable(tween(4200, easing = LinearEasing), RepeatMode.Restart))
            } else null
            if (motion) {
                spread.snapTo(0f)
                intensity.snapTo(0f)
                // Grow and brighten together: the light travels from the centre to the edges.
                launch { spread.animateTo(1f, tween(900, easing = EmphasizedDecelerateEasing)) }
                intensity.animateTo(1f, tween(700, easing = FastOutSlowInEasing))
            } else {
                spread.snapTo(1f)
                intensity.snapTo(1f)
            }
            if (signal.sustained) {
                if (motion) intensity.animateTo(0.62f, infiniteRepeatable(tween(1600, easing = FastOutSlowInEasing), RepeatMode.Reverse))
            } else {
                delay(if (motion) 1400L else 1800L)
                if (motion) intensity.animateTo(0f, tween(1200, easing = FastOutSlowInEasing)) else intensity.snapTo(0f)
                spin?.cancel()
            }
        }
    }
    val layers = remember {
        listOf(
            GlowLayer(strokeDp = 34f, blurDp = 38f, alpha = 0.50f), // wide bloom
            GlowLayer(strokeDp = 12f, blurDp = 14f, alpha = 0.80f), // glow
            GlowLayer(strokeDp = 3f, blurDp = 3f, alpha = 0.95f), // bright core
        )
    }
    val rect = remember { RectF() }
    val matrix = remember { Matrix() }
    val tier = signal?.tier
    val palette = remember(tier) { tier?.glowPalette() }
    drawWithContent {
        drawContent()
        val a = intensity.value
        if (a <= 0.01f || palette == null) return@drawWithContent
        val cx = size.width / 2f
        val cy = size.height / 2f
        val p = spread.value
        // From a small rounded box in the middle out to the screen edges.
        val halfW = cx * (0.18f + 0.82f * p)
        val halfH = cy * (0.10f + 0.90f * p)
        val corner = 40.dp.toPx() * (0.5f + 0.5f * p)
        val shader = SweepGradient(cx, cy, palette, null)
        matrix.setRotate(rotation.value, cx, cy)
        shader.setLocalMatrix(matrix)
        drawIntoCanvas { canvas ->
            layers.forEach { layer ->
                val stroke = layer.strokeDp.dp.toPx()
                val blur = layer.blurDp.dp.toPx()
                if (layer.blurPx != blur) {
                    layer.paint.maskFilter = BlurMaskFilter(blur, BlurMaskFilter.Blur.NORMAL)
                    layer.blurPx = blur
                }
                layer.paint.strokeWidth = stroke
                layer.paint.shader = shader
                layer.paint.alpha = (255 * a * layer.alpha).toInt().coerceIn(0, 255)
                // Centre the stroke on the edge so half the light falls inside the screen.
                rect.set(cx - halfW, cy - halfH, cx + halfW, cy + halfH)
                canvas.nativeCanvas.drawRoundRect(rect, corner, corner, layer.paint)
            }
        }
    }
}
