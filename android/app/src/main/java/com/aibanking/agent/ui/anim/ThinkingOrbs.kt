package com.aibanking.agent.ui.anim

// Native port of thinking-orbs' shaping/20 preset, morph.ts + core.ts.
// Copyright (c) 2026 Jakub Antalik. MIT; full notice in assets/licenses/.
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.isActive
import kotlin.math.*

internal data class OrbDot(val x: Double, val y: Double, val depth: Double, val radius: Double, val ink: Double, val alpha: Double)

/** Arc-length sampled circle → triangle → square, matching upstream shaping/20. */
internal fun shapingOrbFrame(time: Double): List<OrbDot> {
    fun polygon(vertices: List<Pair<Double, Double>>, fraction: Double): Pair<Double, Double> {
        val lengths = vertices.indices.map { index ->
            val a = vertices[index]
            val b = vertices[(index + 1) % vertices.size]
            hypot(b.first - a.first, b.second - a.second)
        }
        var target = fraction * lengths.sum()
        var index = 0
        while (target > lengths[index] && index < vertices.lastIndex) { target -= lengths[index]; index++ }
        val a = vertices[index]
        val b = vertices[(index + 1) % vertices.size]
        val f = if (lengths[index] > 0) min(1.0, target / lengths[index]) else 0.0
        return (a.first + (b.first - a.first) * f) to (a.second + (b.second - a.second) * f)
    }
    fun path(shape: Int, fraction: Double): Pair<Double, Double> = when (shape) {
        0 -> (-PI / 2 + fraction * 2 * PI).let { cos(it) * .24 to sin(it) * .24 }
        1 -> polygon(listOf(0.0 to -.26, .24 to .16, -.24 to .16), fraction)
        else -> polygon(listOf(0.0 to -.2, .2 to -.2, .2 to .2, -.2 to .2, -.2 to -.2), fraction)
    }
    val segmentTime = 1.4 + .9
    val cycleTime = time % (segmentTime * 3)
    val shape = floor(cycleTime / segmentTime).toInt()
    val local = cycleTime - shape * segmentTime
    val blend = if (local > 1.4) ((local - 1.4) / .9).let { it * it * (3 - 2 * it) } else 0.0
    val points = List(160) { index ->
        val a = path(shape, index / 160.0)
        val b = path((shape + 1) % 3, index / 160.0)
        ((a.first + (b.first - a.first) * blend) * 1.45) to ((a.second + (b.second - a.second) * blend) * 1.45)
    }
    val lengths = points.indices.map { index ->
        val a = points[index]
        val b = points[(index + 1) % points.size]
        hypot(b.first - a.first, b.second - a.second)
    }
    val total = lengths.sum()
    val pulse = 1 + .02 * sin(local * 3.1)
    var segment = 0
    var accumulated = 0.0
    return List(18) { index ->
        val target = index / 18.0 * total
        while (accumulated + lengths[segment] < target && segment < points.lastIndex) {
            accumulated += lengths[segment]
            segment++
        }
        val a = points[segment]
        val b = points[(segment + 1) % points.size]
        val fraction = if (lengths[segment] > 0) min(1.0, (target - accumulated) / lengths[segment]) else 0.0
        OrbDot(10 + (a.first + (b.first - a.first) * fraction) * pulse * 20,
            10 + (a.second + (b.second - a.second) * fraction) * pulse * 20,
            0.0, max(.35, .021 * 1.011 * 1.35 * 1.45 * 20), .1, 1.0)
    }
}

@Composable
internal fun ThinkingOrbs(modifier: Modifier = Modifier) {
    val motion = LocalBankMotionEnabled.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val ink = MaterialTheme.colorScheme.onSurface
    var time by remember { mutableDoubleStateOf(.6) }
    LaunchedEffect(motion, lifecycle) {
        if (!motion) { time = .6; return@LaunchedEffect }
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (isActive) withFrameNanos { time = (it / 1_000_000_000.0) * 2.08 }
        }
    }
    Canvas(modifier.size(24.dp).testTag("thinking_orbs").semantics { contentDescription = "安言正在思考" }) {
        val scale = size.minDimension / 20f
        shapingOrbFrame(time).forEach { dot ->
            drawCircle(ink.copy(alpha = ((1 - dot.ink) * dot.alpha).toFloat().coerceIn(0f, 1f)),
                (dot.radius * scale).toFloat(), Offset((dot.x * scale).toFloat(), (dot.y * scale).toFloat()))
        }
    }
}
