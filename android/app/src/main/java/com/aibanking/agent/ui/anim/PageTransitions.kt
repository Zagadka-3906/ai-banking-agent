package com.aibanking.agent.ui.anim

import androidx.compose.animation.AnimatedContentTransitionScope
import androidx.compose.animation.ContentTransform
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.ui.unit.IntOffset

/** Small directional movement preserves navigation context without moving a full page. */
const val PAGE_TRANSITION_MILLIS = 240
val EmphasizedDecelerateEasing = CubicBezierEasing(0.05f, 0.7f, 0.1f, 1f)
val EmphasizedAccelerateEasing = CubicBezierEasing(0.3f, 0f, 0.8f, 0.15f)

/** Compose's animation clock applies the platform animator-duration scale, including zero. */
fun <S> AnimatedContentTransitionScope<S>.pageSlideSpec(depthOf: (S) -> Int): ContentTransform {
    val direction = if (depthOf(targetState) >= depthOf(initialState)) 1 else -1
    val move = tween<IntOffset>(PAGE_TRANSITION_MILLIS, easing = FastOutSlowInEasing)
    return slideInHorizontally(move) { direction * it / 12 } + fadeIn(tween(PAGE_TRANSITION_MILLIS)) togetherWith
        slideOutHorizontally(move) { -direction * it / 20 } + fadeOut(tween(140))
}
