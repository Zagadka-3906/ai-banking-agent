package com.aibanking.agent.ui.anim

import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlin.math.sin

/**
 * 纵深堆叠与错位展开的几何与动效参数。
 */
data class CardStackVisualParams(
    val scale: Float,
    val translationYDp: Dp,
    val translationXDp: Dp,
    val alpha: Float,
    val elevationDp: Dp,
    val zIndex: Float,
)

object CardStackMotion {
    /** 弹性物理回弹动画规格：MediumBouncy 阻尼与低刚度 */
    val StackSpringSpec = spring<Float>(
        dampingRatio = Spring.DampingRatioMediumBouncy,
        stiffness = Spring.StiffnessLow,
    )

    /** 展开时依次错位排开的级联步长 (ms) */
    const val STAGGER_STEP_MS = 55L

    /** 收起时弹性恢复堆叠的反向步长 (ms) */
    const val COLLAPSE_STEP_MS = 30L

    /** 堆叠时每张卡片露出的纵深间距 (dp) */
    val PEEK_OFFSET_PER_CARD = 28.dp

    /**
     * 计算卡片在堆叠态 (progress = 0) 与展开态 (progress = 1) 之间的几何参数。
     *
     * @param index 卡片索引 (0 为顶层前景卡)
     * @param totalCount 卡片总数
     * @param progress 展开进度 (0f = 纵深堆叠, 1f = 完全展开)
     */
    fun computeParams(
        index: Int,
        totalCount: Int,
        progress: Float,
    ): CardStackVisualParams {
        if (totalCount <= 1) {
            return CardStackVisualParams(
                scale = 1.0f,
                translationYDp = 0.dp,
                translationXDp = 0.dp,
                alpha = 1.0f,
                elevationDp = 4.dp,
                zIndex = 1f,
            )
        }

        val clampedProgress = progress.coerceIn(0f, 1f)
        val depth = index.coerceAtLeast(0)

        // 堆叠参数：通过缩放和位移表现前后景深关系
        // 深度越大（靠后的卡片），缩放比例越小（0.94, 0.88...）
        val stackedScale = (1.0f - depth * 0.06f).coerceAtLeast(0.82f)

        // 展开时按顺序错位排开：在展开动画过程中加入左右微错位 flourish
        val staggerDirection = if (index % 2 == 1) 1f else if (index > 0) -1f else 0f
        val wave = sin(clampedProgress * Math.PI.toFloat())
        val staggerOffset = (14f * staggerDirection * wave).dp

        val stackedAlpha = (1.0f - depth * 0.05f).coerceAtLeast(0.75f)
        val stackedElevation = (10f - depth * 2.5f).coerceAtLeast(2f).dp
        // 顶层卡片 zIndex 最高，后方卡片依次递减
        val stackedZIndex = (totalCount - depth + 10).toFloat()

        // 线性/物理插值
        val currentScale = stackedScale + (1.0f - stackedScale) * clampedProgress
        val currentAlpha = stackedAlpha + (1.0f - stackedAlpha) * clampedProgress
        val currentElevation = (stackedElevation.value + (4f - stackedElevation.value) * clampedProgress).dp
        val currentZIndex = if (clampedProgress > 0.85f) (totalCount - depth).toFloat() else stackedZIndex

        return CardStackVisualParams(
            scale = currentScale,
            translationYDp = 0.dp, // Y 轴位移在 Layout 内部结合 placeable.height 精确计算
            translationXDp = staggerOffset,
            alpha = currentAlpha,
            elevationDp = currentElevation,
            zIndex = currentZIndex,
        )
    }

    /**
     * 计算顶层堆叠所需的预留顶部间距（露出后方堆叠卡片的头部）。
     */
    fun computeStackTopPadding(totalCount: Int, progress: Float): Dp {
        if (totalCount <= 1) return 0.dp
        val maxPeek = ((totalCount - 1).coerceAtMost(3) * PEEK_OFFSET_PER_CARD.value).dp
        return (maxPeek.value * (1f - progress.coerceIn(0f, 1f))).dp
    }
}
