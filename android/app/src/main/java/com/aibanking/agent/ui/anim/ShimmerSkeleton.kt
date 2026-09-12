package com.aibanking.agent.ui.anim

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
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
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp

/**
 * 流光骨架屏（Shimmer）→ 真实数据的无缝过渡。
 *
 * 加载中渲染与真实排版一致的灰色占位块，其表面循环播放一道 45° 倾斜的浅色
 * 高光（周期 1200ms）；数据就绪后骨架 200ms 淡出、真实内容同步淡入并带
 * 8dp 的自下而上位移，杜绝突兀刷新。
 *
 * 性能：高光是 draw 期的一层对角线性渐变（无离屏层）；无限动画只存活在
 * 骨架阶段，切换完成后即销毁。
 */
private const val SHIMMER_PERIOD_MS = 1200
private const val CROSSFADE_MS = 200
private val CONTENT_RISE = 8.dp

// 骨架底色与高光：浅灰块 + 半透明白色扫光。
private val SkeletonGray = Color(0xFFE3E7ED)
private val ShimmerHighlight = Color.White.copy(alpha = 0.6f)

/**
 * 状态切换容器：[loading] 为真显示 [skeleton]，数据就绪后交叉淡入 [content]。
 * 真实视图的进场带 8dp 自下而上的位移；骨架原位淡出。
 */
@Composable
fun ShimmerSwitch(
    loading: Boolean,
    modifier: Modifier = Modifier,
    skeleton: @Composable () -> Unit,
    content: @Composable () -> Unit,
) {
    val density = LocalDensity.current
    AnimatedContent(
        targetState = loading,
        modifier = modifier,
        transitionSpec = {
            val fade = tween<Float>(CROSSFADE_MS, easing = FastOutSlowInEasing)
            val slide = tween<IntOffset>(CROSSFADE_MS, easing = FastOutSlowInEasing)
            // 真实视图：淡入 + 8dp 上浮；骨架：原位淡出（规格的 Cross-fade）。
            val enter = fadeIn(fade) + slideInVertically(slide) {
                with(density) { CONTENT_RISE.roundToPx() }
            }
            enter togetherWith fadeOut(fade)
        },
        label = "shimmerSwitch",
    ) { isLoading ->
        if (isLoading) skeleton() else content()
    }
}

/**
 * 骨架占位块的高光扫过：45° 倾斜的浅色带以 1200ms 周期从左上掠过到右下。
 * 只在 draw 期计算渐变，不引入额外布局或离屏层。
 */
fun Modifier.shimmerSweep(): Modifier = composed {
    val transition = rememberInfiniteTransition(label = "shimmer")
    val sweep by transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            tween(SHIMMER_PERIOD_MS, easing = LinearEasing),
            RepeatMode.Restart,
        ),
        label = "shimmerSweep",
    )
    drawWithContent {
        drawContent()
        // 对角（45°）渐变轴：起终点 x/y 位移相等；扫过范围覆盖整块对角线。
        val travel = size.width + size.height
        val x = -size.height + sweep * 2f * travel
        drawRect(
            brush = Brush.linearGradient(
                0f to Color.Transparent,
                0.5f to ShimmerHighlight,
                1f to Color.Transparent,
                start = Offset(x, 0f),
                end = Offset(x + size.height, size.height),
            ),
        )
    }
}

/** 骨架原子：圆角灰色块（可选高光扫过）。 */
@Composable
fun SkeletonBlock(
    width: Dp? = null,
    height: Dp,
    modifier: Modifier = Modifier,
    corner: Dp = 8.dp,
) {
    val base = if (width != null) Modifier.width(width) else Modifier.fillMaxWidth()
    Box(
        modifier
            .then(base)
            .height(height)
            .clip(RoundedCornerShape(corner))
            .background(SkeletonGray)
            .shimmerSweep(),
    )
}

/** 骨架行：圆形头像位 + 两行文字位（联系人行的排版映射）。 */
@Composable
fun SkeletonRow(modifier: Modifier = Modifier) {
    Row(modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(40.dp).clip(CircleShape).background(SkeletonGray).shimmerSweep())
        Column(Modifier.weight(1f).padding(start = 12.dp)) {
            SkeletonBlock(width = 120.dp, height = 14.dp)
            Spacer(Modifier.height(6.dp))
            SkeletonBlock(width = 180.dp, height = 11.dp)
        }
    }
}
