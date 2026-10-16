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
import androidx.compose.ui.graphics.Brush
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
 * A luminous ring in the colour of the latest risk tier. It never pops in: a
 * soft wave of light spreads from the centre of the screen outwards and comes
 * to rest as a glow along the edges. A green reply then fades away; while a
 * yellow or red approval waits, the edge glow keeps breathing until decided.
 * Drawing only - it never takes touches. With system animations off the edge
 * glow simply appears and disappears.
 */
fun Modifier.riskEdgeGlow(signal: RiskSignal?): Modifier = composed {
    val motion = LocalBankMotionEnabled.current
    val intensity = remember { Animatable(0f) }
    val spread = remember { Animatable(1f) }
    // Events already on screen when composed (a restored conversation) or already
    // shown once never flash again.
    val shown = remember { mutableSetOf<String>().apply { signal?.key?.let(::add) } }
    LaunchedEffect(signal) {
        val fresh = signal != null && (signal.sustained || shown.add(signal.key))
        if (signal == null || !fresh) {
            if (motion) intensity.animateTo(0f, tween(700, easing = FastOutSlowInEasing)) else intensity.snapTo(0f)
            return@LaunchedEffect
        }
        if (motion) {
            spread.snapTo(0f)
            intensity.snapTo(1f)
            spread.animateTo(1f, tween(1100, easing = FastOutSlowInEasing))
        } else {
            spread.snapTo(1f)
            intensity.snapTo(1f)
        }
        if (signal.sustained) {
            if (motion) {
                intensity.animateTo(0.5f, infiniteRepeatable(tween(1500, easing = FastOutSlowInEasing), RepeatMode.Reverse))
            }
        } else {
            delay(if (motion) 1200L else 1800L)
            if (motion) intensity.animateTo(0f, tween(1100, easing = LinearEasing)) else intensity.snapTo(0f)
        }
    }
    val color = signal?.tier?.glowColor() ?: Color.Transparent
    drawWithContent {
        drawContent()
        val a = intensity.value
        val p = spread.value
        if (a <= 0.01f) return@drawWithContent
        val center = Offset(size.width / 2f, size.height / 2f)
        val reach = kotlin.math.hypot(size.width / 2f, size.height / 2f)
        // 1. The wave: a wide, soft band of light travelling outwards, dimming as it goes.
        if (p < 1f) {
            val front = (p * 1.05f).coerceIn(0.002f, 1f)
            val tail = (front - 0.45f).coerceAtLeast(0f)
            val waveAlpha = a * 0.42f * (1f - p * 0.7f)
            drawRect(
                Brush.radialGradient(
                    0f to Color.Transparent,
                    tail to Color.Transparent,
                    ((tail + front) / 2f).coerceIn(tail + 0.0005f, front) to color.copy(alpha = waveAlpha * 0.45f),
                    front to color.copy(alpha = waveAlpha),
                    (front + 0.06f).coerceAtMost(1f).coerceAtLeast(front) to Color.Transparent,
                    1f to Color.Transparent,
                    center = center, radius = reach,
                ),
            )
        }
        // 2. The edge glow fades in as the wave arrives at the borders.
        val arrival = ((p - 0.45f) / 0.55f).coerceIn(0f, 1f)
        val edge = a * arrival * arrival * (3f - 2f * arrival)
        if (edge <= 0.01f) return@drawWithContent
        val layers = 44
        val step = 0.9.dp.toPx()
        val corner = 36.dp.toPx()
        for (i in 0 until layers) {
            val t = i / layers.toFloat()
            val inset = step * i + step / 2f
            drawRoundRect(
                color = color.copy(alpha = edge * 0.5f * Math.pow((1.0 - t), 2.4).toFloat()),
                topLeft = Offset(inset, inset),
                size = Size(size.width - inset * 2, size.height - inset * 2),
                cornerRadius = CornerRadius((corner - inset).coerceAtLeast(0f)),
                style = Stroke(width = step * 2.6f),
            )
        }
    }
}
