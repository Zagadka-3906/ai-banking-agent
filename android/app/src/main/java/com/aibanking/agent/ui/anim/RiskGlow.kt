package com.aibanking.agent.ui.anim

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

/** The three risk tiers a server turn can be assigned. */
enum class RiskTier { GREEN, YELLOW, RED }

/** Vivid edge colours, chosen to read on both light and dark canvases. */
private val GlowGreen = Color(0xFF34A853)
private val GlowYellow = Color(0xFFFBBC04)
private val GlowRed = Color(0xFFEA4335)

fun RiskTier.glowColor(): Color = when (this) {
    RiskTier.GREEN -> GlowGreen
    RiskTier.YELLOW -> GlowYellow
    RiskTier.RED -> GlowRed
}

fun riskTierOf(level: String?): RiskTier? = when (level?.uppercase()) {
    "GREEN" -> RiskTier.GREEN
    "YELLOW" -> RiskTier.YELLOW
    "RED" -> RiskTier.RED
    else -> null
}

/**
 * One risk determination to show at the screen edge. [key] identifies the event
 * (a reply or an approval) so the same event never flashes twice; [sustained]
 * keeps the ring breathing while the user still has to decide.
 */
@Immutable
data class RiskSignal(val key: String, val tier: RiskTier, val sustained: Boolean)

/**
 * Draws a soft glow around the screen edges in the colour of the latest risk
 * tier: it lights up when a tier is determined, then fades; while an approval
 * is waiting it keeps breathing gently. Drawing only - it never takes touches.
 * With system animations off the ring is shown without movement.
 */
fun Modifier.riskEdgeGlow(signal: RiskSignal?): Modifier = composed {
    val motion = LocalBankMotionEnabled.current
    val intensity = remember { Animatable(0f) }
    // Events already on screen when composed (a restored conversation) or already
    // shown once never flash again.
    val shown = remember { mutableSetOf<String>().apply { signal?.key?.let(::add) } }
    LaunchedEffect(signal) {
        val fresh = signal != null && (signal.sustained || shown.add(signal.key))
        if (signal == null || !fresh) {
            if (motion) intensity.animateTo(0f, tween(500)) else intensity.snapTo(0f)
            return@LaunchedEffect
        }
        if (motion) intensity.animateTo(1f, tween(280, easing = FastOutSlowInEasing)) else intensity.snapTo(1f)
        if (signal.sustained) {
            if (motion) {
                intensity.animateTo(0.45f, infiniteRepeatable(tween(1300, easing = FastOutSlowInEasing), RepeatMode.Reverse))
            }
        } else {
            delay(if (motion) 1400L else 1800L)
            if (motion) intensity.animateTo(0f, tween(900, easing = LinearEasing)) else intensity.snapTo(0f)
        }
    }
    val color = signal?.tier?.glowColor() ?: Color.Transparent
    drawWithContent {
        drawContent()
        val a = intensity.value
        if (a <= 0.01f) return@drawWithContent
        // Concentric strokes fading inward approximate a blurred light band.
        val layers = 40
        val step = 0.8.dp.toPx()
        val corner = 36.dp.toPx()
        for (i in 0 until layers) {
            val t = i / layers.toFloat()
            val inset = step * i + step / 2f
            drawRoundRect(
                color = color.copy(alpha = a * 0.55f * Math.pow((1.0 - t), 2.2).toFloat()),
                topLeft = Offset(inset, inset),
                size = Size(size.width - inset * 2, size.height - inset * 2),
                cornerRadius = CornerRadius((corner - inset).coerceAtLeast(0f)),
                style = Stroke(width = step * 2.5f),
            )
        }
    }
}
