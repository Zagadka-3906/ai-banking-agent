package com.aibanking.agent.ui.anim

import androidx.compose.animation.AnimatedVisibilityScope
import androidx.compose.animation.EnterExitState
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.tween
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.dp

/** Kept for source compatibility: legacy cube callers now receive a quiet fade and rise. */
const val CUBE_FLIP_MILLIS = 220

fun Modifier.cubeFlip(scope: AnimatedVisibilityScope): Modifier = composed {
    if (!LocalBankMotionEnabled.current) return@composed this
    val progress = scope.transition.animateFloat(
        transitionSpec = { tween(CUBE_FLIP_MILLIS, easing = FastOutSlowInEasing) },
        label = "surfaceReveal",
        targetValueByState = { if (it == EnterExitState.Visible) 1f else 0f },
    )
    graphicsLayer {
        alpha = progress.value
        translationY = (1f - progress.value) * 12.dp.toPx()
    }
}
