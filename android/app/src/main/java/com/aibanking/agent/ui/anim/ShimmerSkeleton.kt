package com.aibanking.agent.ui.anim

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import com.valentinilk.shimmer.shimmer

/** Data and placeholder share a stable container, then crossfade with an 8 dp rise. */
@Composable
fun ShimmerSwitch(loading: Boolean, modifier: Modifier = Modifier, skeleton: @Composable () -> Unit, content: @Composable () -> Unit) {
    if (!LocalBankMotionEnabled.current) {
        Box(modifier) { if (loading) skeleton() else content() }
        return
    }
    val density = LocalDensity.current
    AnimatedContent(
        targetState = loading,
        modifier = modifier,
        transitionSpec = {
            val fade = tween<Float>(180, easing = FastOutSlowInEasing)
            val slide = tween<IntOffset>(180, easing = FastOutSlowInEasing)
            fadeIn(fade) + slideInVertically(slide) { with(density) { 8.dp.roundToPx() } } togetherWith fadeOut(fade)
        },
        label = "loadingContent",
    ) { isLoading -> if (isLoading) skeleton() else content() }
}

/** Place the shimmer draw modifier before the background it animates. */
fun Modifier.shimmerSweep(): Modifier = composed {
    if (LocalBankMotionEnabled.current) shimmer() else this
}

@Composable
fun SkeletonBlock(height: Dp, modifier: Modifier = Modifier, width: Dp? = null, corner: Dp = 8.dp) {
    val base = if (width != null) Modifier.width(width) else Modifier.fillMaxWidth()
    Box(modifier.then(base).height(height).clip(RoundedCornerShape(corner)).shimmerSweep().background(MaterialTheme.colorScheme.surfaceContainerHighest))
}

@Composable
fun SkeletonRow(modifier: Modifier = Modifier) {
    Row(modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(44.dp).clip(CircleShape).shimmerSweep().background(MaterialTheme.colorScheme.surfaceContainerHighest))
        Column(Modifier.weight(1f).padding(start = 14.dp)) {
            SkeletonBlock(width = 120.dp, height = 14.dp)
            Spacer(Modifier.height(8.dp))
            SkeletonBlock(width = 180.dp, height = 11.dp)
        }
    }
}
