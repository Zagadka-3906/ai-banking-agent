package com.aibanking.agent.ui.anim

// Native port of thinking-orbs' working/20 preset, orbits.ts + core.ts.
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

/** Same deterministic geometry and 20px tuning as upstream's working state. */
internal fun workingOrbFrame(time: Double): List<OrbDot> {
    val dots = ArrayList<OrbDot>(39)
    val radius = 10 * .82
    val scale = (20.0 / 300).pow(.6) * 2.4
    fun hash(a: Double, b: Double): Double = (sin(a * 12.9898 + b * 78.233) * 43758.5453).let { it - floor(it) }
    fun project(x: Double, y: Double, z: Double): Triple<Double, Double, Double> {
        val yaw = time * .12
        val x1 = x * cos(yaw) + z * sin(yaw)
        val z1 = -x * sin(yaw) + z * cos(yaw)
        return Triple(10 + x1, 10 - (y * cos(.3) - z1 * sin(.3)), y * sin(.3) + z1 * cos(.3))
    }
    repeat(3) { orbit ->
        val h1 = hash(orbit.toDouble(), 1.7)
        val h2 = hash(orbit.toDouble(), 5.2)
        val h3 = hash(orbit.toDouble(), 8.9)
        val r = radius * (.45 + .52 * h1)
        val theta = h1 * 2 * PI
        val phi = acos(2 * h2 - 1)
        val nx = sin(phi) * cos(theta)
        val ny = cos(phi)
        val nz = sin(phi) * sin(theta)
        val length = max(1e-6, sqrt(ny * ny + nx * nx))
        val ux = -ny / length
        val uy = nx / length
        val vx = -nz * uy
        val vy = nz * ux
        val vz = nx * uy - ny * ux
        fun dot(angle: Double, ghost: Boolean) {
            val (x, y, z) = project((ux * cos(angle) + vx * sin(angle)) * r,
                (uy * cos(angle) + vy * sin(angle)) * r, vz * sin(angle) * r)
            val depth = (z / r + 1) / 2
            dots += OrbDot(x, y, z, max(.3, (if (ghost) .9 else 1.2 + 1.6 * depth) * scale),
                if (ghost) .72 else .3 - .22 * depth, if (ghost) .5 * (.4 + .6 * depth) else 1.0)
        }
        repeat(10) { dot(it / 10.0 * 2 * PI, true) }
        val speed = (.25 + .55 * h3) * if (h3 > .5) 1 else -1
        repeat(3) { dot(time * speed + it / 3.0 * 2 * PI + h2 * 6, false) }
    }
    return dots.sortedBy { it.depth }
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
            while (isActive) withFrameNanos { time = (it / 1_000_000_000.0) * 3.9 }
        }
    }
    Canvas(modifier.size(24.dp).testTag("thinking_orbs").semantics { contentDescription = "安言正在思考" }) {
        val scale = size.minDimension / 20f
        workingOrbFrame(time).forEach { dot ->
            drawCircle(ink.copy(alpha = ((1 - dot.ink) * dot.alpha).toFloat().coerceIn(0f, 1f)),
                (dot.radius * scale).toFloat(), Offset((dot.x * scale).toFloat(), (dot.y * scale).toFloat()))
        }
    }
}
