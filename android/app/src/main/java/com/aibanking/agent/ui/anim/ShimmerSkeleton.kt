package com.aibanking.agent.ui.anim

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedContentTransitionScope
import androidx.compose.animation.ContentTransform
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.snap
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.valentinilk.shimmer.ShimmerTheme
import com.valentinilk.shimmer.defaultShimmerTheme
import com.valentinilk.shimmer.shimmer

/**
 * 流光骨架屏动效与过渡规格：
 * 1. 骨架动效：呈现与实际排版一致的灰色占位色块，表面循环播放 45° 倾斜高光扫过动画（Shimmer Sweep，周期 1200ms）。
 * 2. 内容就绪过渡：数据加载完成时，骨架容器以 200ms fadeOut，真实视图同时 fadeIn 并伴随微量自下而上的位移（8dp -> 0dp）。
 */
const val SHIMMER_SWEEP_PERIOD_MILLIS = 1200
const val SHIMMER_SWEEP_ANGLE_DEGREES = 45f
const val SHIMMER_FADE_DURATION_MILLIS = 200
val SHIMMER_RISE_OFFSET = 8.dp

/**
 * 构造统一的 Shimmer 主题配置：
 * - 45° 倾斜角度 (SHIMMER_SWEEP_ANGLE_DEGREES)
 * - 周期 1200ms 的浅色高光扫过动画 (SHIMMER_SWEEP_PERIOD_MILLIS)
 */
fun bankShimmerTheme(darkTheme: Boolean = false): ShimmerTheme = defaultShimmerTheme.copy(
    animationSpec = infiniteRepeatable(
        animation = tween<Float>(
            durationMillis = SHIMMER_SWEEP_PERIOD_MILLIS,
            easing = LinearEasing,
        ),
    ),
    rotation = SHIMMER_SWEEP_ANGLE_DEGREES,
    shaderColors = if (darkTheme) {
        listOf(
            Color.White.copy(alpha = 0.12f),
            Color.White.copy(alpha = 0.38f),
            Color.White.copy(alpha = 0.12f),
        )
    } else {
        listOf(
            Color.White.copy(alpha = 0.25f),
            Color.White.copy(alpha = 0.65f),
            Color.White.copy(alpha = 0.25f),
        )
    },
    shimmerWidth = 240.dp,
)

/**
 * 构造流光骨架屏到真实视图的过渡转场规格。
 */
fun shimmerTransitionSpec(
    density: Density,
    motionEnabled: Boolean = true,
    riseOffset: Dp = SHIMMER_RISE_OFFSET,
    durationMillis: Int = SHIMMER_FADE_DURATION_MILLIS,
): AnimatedContentTransitionScope<Boolean>.() -> ContentTransform = {
    if (!motionEnabled) {
        fadeIn(snap()) togetherWith fadeOut(snap())
    } else if (initialState && !targetState) {
        // 数据加载完成：骨架容器以 200ms fadeOut，真实视图同时 fadeIn 并伴随微量自下而上的位移（8dp -> 0dp）
        (fadeIn(tween(durationMillis, easing = FastOutSlowInEasing)) +
            slideInVertically(tween(durationMillis, easing = FastOutSlowInEasing)) {
                with(density) { riseOffset.roundToPx() }
            }) togetherWith fadeOut(tween(durationMillis, easing = FastOutSlowInEasing))
    } else {
        // 重新进入加载状态：平滑交叉淡入淡出
        fadeIn(tween(durationMillis, easing = FastOutSlowInEasing)) togetherWith
            fadeOut(tween(durationMillis, easing = FastOutSlowInEasing))
    }
}

/**
 * 从「流光骨架屏（Shimmer）」到真实数据的无缝过渡动效容器。
 */
@Composable
fun ShimmerSwitch(
    loading: Boolean,
    modifier: Modifier = Modifier,
    skeleton: @Composable () -> Unit,
    content: @Composable () -> Unit,
) {
    val motion = LocalBankMotionEnabled.current
    val density = LocalDensity.current
    AnimatedContent(
        targetState = loading,
        modifier = modifier,
        transitionSpec = shimmerTransitionSpec(density, motion),
        label = "shimmerSwitch",
    ) { isLoading ->
        if (isLoading) skeleton() else content()
    }
}

/** Place the shimmer draw modifier before the background it animates. */
fun Modifier.shimmerSweep(): Modifier = composed {
    if (LocalBankMotionEnabled.current) shimmer() else this
}

@Composable
fun SkeletonBlock(height: Dp, modifier: Modifier = Modifier, width: Dp? = null, corner: Dp = 8.dp) {
    val base = if (width != null) Modifier.width(width) else Modifier.fillMaxWidth()
    Box(
        modifier
            .then(base)
            .height(height)
            .clip(RoundedCornerShape(corner))
            .shimmerSweep()
            .background(MaterialTheme.colorScheme.surfaceContainerHighest)
    )
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
