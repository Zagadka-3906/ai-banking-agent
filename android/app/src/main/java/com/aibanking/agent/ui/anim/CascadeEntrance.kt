package com.aibanking.agent.ui.anim

import android.os.SystemClock
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

/** A short entrance window prevents rows encountered during scrolling from replaying. */
class CascadeEntranceState {
    internal var generation: Int by mutableIntStateOf(0)
    internal var windowOpenedAtMs: Long = Long.MIN_VALUE
    fun begin() {
        windowOpenedAtMs = SystemClock.uptimeMillis()
        generation += 1
    }
}

@Composable
fun rememberCascadeEntrance(ready: Boolean): CascadeEntranceState {
    val state = remember { CascadeEntranceState() }
    val inBackPreview = LocalInBackPreviewBeneath.current
    val motion = LocalBankMotionEnabled.current
    LaunchedEffect(ready, inBackPreview, motion) {
        if (ready && !inBackPreview && motion) state.begin()
    }
    return state
}

fun Modifier.cascadeItem(host: CascadeEntranceState, index: Int): Modifier = composed {
    if (LocalInBackPreviewBeneath.current || !LocalBankMotionEnabled.current) return@composed this
    val generation = host.generation
    val participates = remember(host, generation, index) {
        index in 0 until 6 && host.windowOpenedAtMs > Long.MIN_VALUE &&
            SystemClock.uptimeMillis() - host.windowOpenedAtMs <= 450L
    }
    val animation = remember(host, generation, index) { Animatable(if (participates) 0f else 1f) }
    if (participates) {
        LaunchedEffect(host, generation, index) {
            delay(index * 28L)
            animation.animateTo(1f, tween(220, easing = FastOutSlowInEasing))
        }
    }
    graphicsLayer {
        alpha = animation.value
        translationY = (1f - animation.value) * 12.dp.toPx()
    }
}
