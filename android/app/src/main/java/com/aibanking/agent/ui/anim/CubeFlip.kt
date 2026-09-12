package com.aibanking.agent.ui.anim

import androidx.compose.animation.AnimatedVisibilityScope
import androidx.compose.animation.EnterExitState
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.tween
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.zIndex

/**
 * 3D 立方体翻页（Cube Flip）：页面切换时旧页绕**右缘** 0°→-90° 翻出（带轻
 * 微阴影暗角），新页绕**左缘** 90°→0° 翻入，两页拼成一个绕公共棱转动的立方体。
 *
 * 用法： AnimatedContent 的 transitionSpec 给 EnterTransition.None
 * togetherWith ExitTransition.None（进出场零动画），逐页套 [cubeFlip]，转场
 * 完全由本修饰符按各页的 enter/exit 进度驱动。
 *
 * 实现与性能：
 *  * 透视来自 RenderNode 的相机距离（≈4 倍页宽）：边缘有明显透视畸变，但
 *    翻到 ±90° 时面板恰好侧棱对屏，最远端深度只有相机距离的 1/4，不会穿过
 *    相机平面产生破损；
 *  * 逐帧只更新 graphicsLayer 的旋转矩阵/原点/相机距离与 draw 期的一层黑
 *    色暗角——全部是 RenderNode 硬件属性，页面树零重组、无离屏重绘；
 *  * 退场页 zIndex 压过进场页：翻折中两页投影在屏幕中部交叠时遮挡关系恒
 *    定，不出现深度闪烁；
 *  * 进度曲线 350ms FastOutSlowIn，进场/退场共用同一节奏，立方体两翼同步。
 */
const val CUBE_FLIP_MILLIS = 350

// 退场页暗角上限：轻微阴影暗示“转走”，不遮内容。
private const val MAX_FLIP_DIM = 0.3f

fun Modifier.cubeFlip(scope: AnimatedVisibilityScope): Modifier = composed {
    // 本页的可见度进度：进场 0→1、退场 1→0（稳态恒为 1 = 恒等矩阵）。
    val progress = scope.transition.animateFloat(
        transitionSpec = { tween(CUBE_FLIP_MILLIS, easing = FastOutSlowInEasing) },
        targetValueByState = { if (it == EnterExitState.Visible) 1f else 0f },
    )
    // 退场页 vs 进场页由转场目标决定；稳态（Visible）走进场分支 = 恒等。
    val exiting = scope.transition.targetState != EnterExitState.Visible
    Modifier
        .zIndex(if (exiting) 1f else 0f)
        .graphicsLayer {
            val p = progress.value
            rotationY = (if (exiting) -90f else 90f) * (1f - p)
            // 旋转中心锁在页缘：退场右缘、进场左缘（规格的 Transform Origin）。
            transformOrigin = if (exiting) TransformOrigin(1f, 0.5f) else TransformOrigin(0f, 0.5f)
            cameraDistance = size.width * 4f
        }
        .drawWithContent {
            drawContent()
            if (exiting) {
                val dim = (1f - progress.value) * MAX_FLIP_DIM
                if (dim > 0f) drawRect(Color.Black.copy(alpha = dim))
            }
        }
}
