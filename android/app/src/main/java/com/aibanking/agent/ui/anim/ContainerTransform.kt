package com.aibanking.agent.ui.anim

import androidx.compose.animation.AnimatedVisibilityScope
import androidx.compose.animation.BoundsTransform
import androidx.compose.animation.EnterExitState
import androidx.compose.animation.ExperimentalSharedTransitionApi
import androidx.compose.animation.SharedTransitionScope
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.Easing
import androidx.compose.animation.core.animateDp
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Outline
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp

/**
 * Material Design 3 容器变换（Container Transform）规范：
 * 1. 时长 350ms，使用强调减速曲线（Emphasized Decelerate Easing: CubicBezier(0.05f, 0.7f, 0.1f, 1f)）。
 * 2. 形状渐变：从圆角卡片（例如 16dp / 24dp）平滑过渡到直角/大屏界面（0dp）。
 * 3. 遮罩裁剪（ClipToBounds）：在尺寸缩放的同时同步裁剪内部内容，避免文字或布局拉伸溢出。
 * 4. 层级变化：在动画过渡期间提升 Elevation/阴影（例如提升至 8dp），形成“从底层悬浮并展开”的空间层次感。
 */
const val CONTAINER_TRANSFORM_DURATION_MILLIS = 350

val ContainerTransformEasing: Easing = CubicBezierEasing(0.05f, 0.7f, 0.1f, 1f)

@OptIn(ExperimentalSharedTransitionApi::class)
fun containerTransformBoundsTransform(motionEnabled: Boolean = true): BoundsTransform =
    BoundsTransform { _, _ ->
        if (motionEnabled) {
            tween(
                durationMillis = CONTAINER_TRANSFORM_DURATION_MILLIS,
                easing = ContainerTransformEasing,
            )
        } else {
            snap()
        }
    }

@OptIn(ExperimentalSharedTransitionApi::class)
class MorphShapeOverlayClip(private val shape: Shape) : SharedTransitionScope.OverlayClip {
    override fun getClipPath(
        sharedContentState: SharedTransitionScope.SharedContentState,
        bounds: Rect,
        layoutDirection: LayoutDirection,
        density: Density
    ): Path? {
        val size = Size(bounds.width.coerceAtLeast(1f), bounds.height.coerceAtLeast(1f))
        val outline = shape.createOutline(size, layoutDirection, density)
        return when (outline) {
            is Outline.Rectangle -> Path().apply { addRect(outline.rect) }
            is Outline.Rounded -> Path().apply { addRoundRect(outline.roundRect) }
            is Outline.Generic -> outline.path
        }
    }
}

/**
 * 为容器赋予 Material Design 3 容器变换修饰符。
 *
 * @param sharedTransitionScope 共享元素转场作用域
 * @param animatedVisibilityScope 动画可见性作用域
 * @param key 唯一容器变换键（如 "contact-container-${contact.contactId}"）
 * @param cardCorner 卡片形态圆角半径（默认 16dp）
 * @param detailCorner 详情页形态圆角半径（默认 0dp 直角）
 * @param cardElevation 列表卡片静止时的阴影高度（默认 2dp）
 * @param elevatedElevation 转场动画过程中的悬浮提升阴影高度（默认 8dp）
 * @param detailElevation 详情页面铺满静止时的阴影高度（默认 0dp）
 * @param containerColor 容器底色（在 Overlay 转场时保持不透明填充，防止透底下层内容）
 * @param isDetail 当前容器是否为详情页形态（true 为详情，false 为列表卡片）
 */
@OptIn(ExperimentalSharedTransitionApi::class)
fun Modifier.bankContainerTransform(
    sharedTransitionScope: SharedTransitionScope?,
    animatedVisibilityScope: AnimatedVisibilityScope?,
    key: String,
    cardCorner: Dp = 16.dp,
    detailCorner: Dp = 0.dp,
    cardElevation: Dp = 2.dp,
    elevatedElevation: Dp = 8.dp,
    detailElevation: Dp = 0.dp,
    containerColor: Color = Color.Unspecified,
    isDetail: Boolean = false,
): Modifier = composed {
    if (sharedTransitionScope == null || animatedVisibilityScope == null) return@composed this

    val motion = LocalBankMotionEnabled.current
    val transition = animatedVisibilityScope.transition

    val cornerRadius by transition.animateDp(
        transitionSpec = {
            if (motion) tween(CONTAINER_TRANSFORM_DURATION_MILLIS, easing = ContainerTransformEasing)
            else snap()
        },
        label = "containerCorner_$key",
    ) { enterExitState ->
        when (enterExitState) {
            EnterExitState.Visible -> if (isDetail) detailCorner else cardCorner
            EnterExitState.PreEnter -> if (isDetail) cardCorner else detailCorner
            EnterExitState.PostExit -> if (isDetail) cardCorner else detailCorner
        }
    }

    val targetElevation = when {
        transition.isRunning -> elevatedElevation
        transition.targetState == EnterExitState.Visible -> if (isDetail) detailElevation else cardElevation
        else -> cardElevation
    }

    val elevation by animateDpAsState(
        targetValue = targetElevation,
        animationSpec = if (motion) tween(CONTAINER_TRANSFORM_DURATION_MILLIS, easing = ContainerTransformEasing) else snap(),
        label = "containerElevation_$key",
    )

    val currentShape = RoundedCornerShape(cornerRadius)

    val backgroundModifier = if (containerColor != Color.Unspecified) {
        Modifier.background(containerColor, currentShape)
    } else {
        Modifier
    }

    this
        .shadow(
            elevation = elevation,
            shape = currentShape,
            clip = false,
        )
        .then(backgroundModifier)
        .then(
            with(sharedTransitionScope) {
                Modifier.sharedBounds(
                    sharedContentState = rememberSharedContentState(key = key),
                    animatedVisibilityScope = animatedVisibilityScope,
                    boundsTransform = containerTransformBoundsTransform(motion),
                    resizeMode = SharedTransitionScope.ResizeMode.RemeasureToBounds,
                    clipInOverlayDuringTransition = MorphShapeOverlayClip(currentShape),
                    zIndexInOverlay = 3f,
                )
            }
        )
        .clip(currentShape)
}
