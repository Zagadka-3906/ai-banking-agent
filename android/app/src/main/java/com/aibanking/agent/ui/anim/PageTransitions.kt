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

/**
 * 页面级“推入/弹出”转场的统一规格（官方 androidx.compose.animation API）。
 *
 * 前进（推入）：新页从右侧整幅平移进入并淡入，旧页向左平移约 1/4 屏宽
 * （20%~30% 区间）并淡出；返回（弹出）时严格逆向执行对应的动画。
 *
 * 动画只改位移与 alpha，逐帧由 AnimatedContent 写在 graphicsLayer 上，
 * 内容本身不参与逐帧重组；快出慢入曲线 + 300ms 时长保证跟手、不拖沓。
 */
const val PAGE_TRANSITION_MILLIS = 300

/**
 * Material 3 的「强调（Emphasized）」运动曲线标准贝塞尔控制点：
 * 展开用减速（快出慢停，强调结果），收回用加速（慢起快收，强调来源）。
 */
val EmphasizedDecelerateEasing = CubicBezierEasing(0.05f, 0.7f, 0.1f, 1f)
val EmphasizedAccelerateEasing = CubicBezierEasing(0.3f, 0f, 0.8f, 0.15f)

/**
 * [depthOf] 给每个页面状态一个“层级深度”：目标比起点深视为前进，
 * 反之视为返回。聊天 ↔ 卡片/理财/联系人、列表 ↔ 详情这类层级导航
 * 都直接用枚举的 [Enum.ordinal] 表达深度即可。
 */
fun <S> AnimatedContentTransitionScope<S>.pageSlideSpec(depthOf: (S) -> Int): ContentTransform {
    val move = tween<IntOffset>(PAGE_TRANSITION_MILLIS, easing = FastOutSlowInEasing)
    val alpha = tween<Float>(PAGE_TRANSITION_MILLIS, easing = FastOutSlowInEasing)
    return if (depthOf(targetState) >= depthOf(initialState)) {
        slideInHorizontally(move) { it } + fadeIn(alpha) togetherWith
            slideOutHorizontally(move) { -it / 4 } + fadeOut(alpha)
    } else {
        slideInHorizontally(move) { -it / 4 } + fadeIn(alpha) togetherWith
            slideOutHorizontally(move) { it } + fadeOut(alpha)
    }
}
