package com.aibanking.agent.ui.chat

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathMeasure
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * The status mark at the top of the result page.
 *
 * Success: the green disc springs in on a soft halo, the tick draws itself in
 * one stroke, and a ring ripples outward twice as it completes. Failure draws
 * its cross the same way; a result still being reconciled shows a turning arc.
 * With system animations off, the finished mark is drawn at once.
 */
@Composable
internal fun OutcomeBadge(
    tone: OutcomeTone,
    container: Color,
    mark: Color,
    modifier: Modifier = Modifier,
    size: Dp = 96.dp,
) {
    val motion = LocalBankMotionEnabled.current
    val disc = remember { Animatable(if (motion) 0f else 1f) }
    val stroke = remember { Animatable(if (motion) 0f else 1f) }
    val ripple = remember { Animatable(0f) }
    val spin = remember { Animatable(0f) }
    LaunchedEffect(tone, motion) {
        if (!motion) return@LaunchedEffect
        coroutineScope {
            if (tone == OutcomeTone.PENDING) {
                launch { spin.animateTo(360f, infiniteRepeatable(tween(1100, easing = LinearEasing), RepeatMode.Restart)) }
            }
            // A softer, slower spring: the disc settles instead of snapping in.
            disc.animateTo(1f, spring(dampingRatio = 0.6f, stiffness = 140f))
        }
    }
    LaunchedEffect(tone, motion) {
        if (!motion) return@LaunchedEffect
        delay(420)
        stroke.animateTo(1f, tween(if (tone == OutcomeTone.FAILURE) 860 else 820, easing = FastOutSlowInEasing))
        if (tone == OutcomeTone.SUCCESS) {
            repeat(2) {
                ripple.snapTo(0f)
                ripple.animateTo(1f, tween(1200, easing = FastOutSlowInEasing))
            }
        }
    }
    val path = remember { Path() }
    val measure = remember { PathMeasure() }
    val partial = remember { Path() }
    Box(modifier.size(size * 1.6f), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(size * 1.6f)) {
            val center = Offset(this.size.width / 2f, this.size.height / 2f)
            val radius = size.toPx() / 2f
            val d = disc.value
            // Soft halo that blooms with the disc.
            drawCircle(
                Brush.radialGradient(listOf(container.copy(alpha = 0.55f * d.coerceIn(0f, 1f)), Color.Transparent),
                    center = center, radius = radius * 1.6f),
                radius = radius * 1.6f, center = center,
            )
            // Ripple rings after the tick completes.
            val r = ripple.value
            if (r > 0f && r < 1f) {
                drawCircle(mark.copy(alpha = 0.35f * (1f - r)), radius = radius * (1f + 0.55f * r), center = center,
                    style = Stroke(width = 2.dp.toPx()))
            }
            drawCircle(container, radius = radius * d, center = center)
            if (d < 0.6f) return@Canvas
            val width = radius * 0.13f
            when (tone) {
                OutcomeTone.SUCCESS -> {
                    path.reset()
                    path.moveTo(center.x - radius * 0.38f, center.y + radius * 0.02f)
                    path.lineTo(center.x - radius * 0.10f, center.y + radius * 0.30f)
                    path.lineTo(center.x + radius * 0.42f, center.y - radius * 0.28f)
                    drawTrimmed(path, measure, partial, stroke.value, mark, width)
                }
                OutcomeTone.FAILURE -> {
                    val arm = radius * 0.30f
                    val first = (stroke.value * 2f).coerceIn(0f, 1f)
                    val second = (stroke.value * 2f - 1f).coerceIn(0f, 1f)
                    path.reset()
                    path.moveTo(center.x - arm, center.y - arm)
                    path.lineTo(center.x + arm, center.y + arm)
                    drawTrimmed(path, measure, partial, first, mark, width)
                    path.reset()
                    path.moveTo(center.x + arm, center.y - arm)
                    path.lineTo(center.x - arm, center.y + arm)
                    drawTrimmed(path, measure, partial, second, mark, width)
                }
                OutcomeTone.PENDING -> {
                    val inset = radius * 0.42f
                    drawArc(mark, startAngle = spin.value - 90f, sweepAngle = 100f + 160f * stroke.value, useCenter = false,
                        topLeft = Offset(center.x - inset, center.y - inset), size = Size(inset * 2, inset * 2),
                        style = Stroke(width = width, cap = StrokeCap.Round))
                }
                OutcomeTone.NEUTRAL -> {
                    // A pause bar pair: the operation was stopped, not failed.
                    val h = radius * 0.32f * stroke.value
                    for (x in floatArrayOf(-0.14f, 0.14f)) {
                        drawLine(mark, Offset(center.x + radius * x, center.y - h), Offset(center.x + radius * x, center.y + h),
                            strokeWidth = width, cap = StrokeCap.Round)
                    }
                }
            }
        }
    }
}

private fun DrawScope.drawTrimmed(path: Path, measure: PathMeasure, partial: Path, fraction: Float, color: Color, width: Float) {
    if (fraction <= 0f) return
    measure.setPath(path, false)
    partial.reset()
    measure.getSegment(0f, measure.length * fraction.coerceIn(0f, 1f), partial, true)
    drawPath(partial, color, style = Stroke(width = width, cap = StrokeCap.Round, join = StrokeJoin.Round))
}
