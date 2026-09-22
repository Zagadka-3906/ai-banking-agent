package com.aibanking.agent.ui.anim

import androidx.activity.BackEventCompat
import androidx.activity.compose.PredictiveBackHandler
import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.dp
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.launch

/**
 * 预测性返回手势（Predictive Back）的“跟手预览”层。
 *
 * 用户从屏幕边缘内滑时，[PredictiveBackHandler] 以 0..1 的进度回报手指位移；
 * 本组件把进度直接映射到两层变换上，页面跟着手指走而不是等返回落地才动画：
 *
 *  * 顶层（当前页）：等比缩放 1.0 → 0.96，圆角 0 → 24dp，向手势侧轻微位移，
 *    并随进度升起投影——与系统预测性返回的层级观感一致；
 *  * 底层（被露出的上一层级页）：从手势对侧视差推入约 6% 屏宽，透明度
 *    0.92 → 1.0，位移方向与手指一致但幅度更小，形成纵深视差。
 *
 * 手势完成（越过提交阈值）：立即收起底层预览并执行 [onBack]（真正的状态切
 * 换，由调用方既有的页面转场接力演出），顶层变换在 180ms 内解除，两段动画
 * 方向一致、观感连续。手势取消：进度用高阻尼弹簧平滑弹回原位。
 *
 * 注意 manifest 需要 `android:enableOnBackInvokedCallback="true"`，否则系统
 * 不下发逐帧进度，手势退化为普通返回（本组件依然正确提交）。
 */

// 顶层页面规格：缩放 1.0 → 0.96、圆角 → 24dp、轻微跟手位移与投影。
private const val TOP_MAX_SHRINK = 0.04f
private const val TOP_MAX_SHIFT = 0.03f
private val TOP_MAX_CORNER = 24.dp
private const val TOP_MAX_SHADOW_DP = 8f

// 底层页面规格：视差推入行程 6% 屏宽、透明度 0.92 → 1.0。
private const val BENEATH_MAX_PARALLAX = 0.06f
private const val BENEATH_MIN_ALPHA = 0.92f

// 取消回弹：无过冲弹簧，保持手势取消后的稳定收束。
private val CancelSpring = spring<Float>(
    dampingRatio = Spring.DampingRatioNoBouncy,
    stiffness = Spring.StiffnessMediumLow,
)

// 提交后的解除：固定短 tween，随正式转场收尾，不与页面动画抢戏。
private val CommitUnwind = tween<Float>(180)

/**
 * 底层预览标记：预览期间底层页与顶层页同时在场，若底层带有与顶层配对的
 * 共享元素（如联系人列表行），同 key 元素在转场外同时可见并无意义，消费方
 * （Modifier.contactShared）据此退化为普通绘制。
 */
val LocalInBackPreviewBeneath = compositionLocalOf { false }

/**
 * 包住一层“可被返回的页面栈顶”。启用时接管预测性返回：手势进度驱动变换，
 * 提交执行 [onBack]，取消弹回。[beneath] 传“返回后将露出的页面”，手势期间
 * 绘制在顶层之下；没有更浅层级（返回即退出应用）时留空，仅保留顶层变换。
 */
@Composable
fun PredictiveBackPreview(
    enabled: Boolean,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
    beneath: (@Composable () -> Unit)? = null,
    content: @Composable () -> Unit,
) {
    if (!LocalBankMotionEnabled.current) {
        BackHandler(enabled = enabled, onBack = onBack)
        Box(modifier.fillMaxSize()) { content() }
        return
    }
    val progress = remember { Animatable(0f) }
    var edgeSign by remember { mutableFloatStateOf(1f) }
    // 底层预览的挂载开关只在「手势出现第一帧 / 提交 / 回弹结束」三个离散时刻
    // 翻转：逐帧进度只允许被 graphicsLayer 块读取（图层属性，不触发重组），
    // 手势全程页面树零重组。
    var showBeneath by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    PredictiveBackHandler(enabled = enabled) { events ->
        try {
            events.collect { event ->
                edgeSign = if (event.swipeEdge == BackEventCompat.EDGE_LEFT) 1f else -1f
                showBeneath = true
                progress.snapTo(event.progress)
            }
            // 流正常结束 = 系统判定提交。立即收起底层预览（避免与正式转场短暂
            // 双渲染同一页面），切换状态交给既有页面动画，再快速解除顶层变换。
            showBeneath = false
            onBack()
            scope.launch { progress.animateTo(0f, CommitUnwind) }
        } catch (failure: CancellationException) {
            // 提前松手或移回边缘：按规格用高阻尼弹簧平滑弹回原位，
            // 底层预览跟随弹簧一起淡出，动画结束才撤下。
            scope.launch {
                progress.animateTo(0f, CancelSpring)
                showBeneath = false
            }
        }
    }

    Box(modifier.fillMaxSize()) {
        if (beneath != null && showBeneath) {
            CompositionLocalProvider(LocalInBackPreviewBeneath provides true) {
                Box(
                    Modifier
                        .matchParentSize()
                        .graphicsLayer {
                            // 底层跟随手指同向推入：起点偏移 6% 屏宽，随进度归零；
                            // 透明度从 0.92 恢复到 1.0，读作“从深处浮现”。
                            val p = progress.value
                            translationX = -edgeSign * BENEATH_MAX_PARALLAX * (1f - p) * size.width
                            alpha = BENEATH_MIN_ALPHA + (1f - BENEATH_MIN_ALPHA) * p
                        },
                ) { beneath() }
            }
        }
        Box(
            Modifier
                .matchParentSize()
                .graphicsLayer {
                    // 无分支恒等式写法：p=0 时全部归位，图层不会残留上一帧变换。
                    // 只写图层属性、不触发重组，逐帧由 RenderNode 合成。
                    val p = progress.value
                    val scale = 1f - TOP_MAX_SHRINK * p
                    scaleX = scale
                    scaleY = scale
                    translationX = edgeSign * TOP_MAX_SHIFT * p * size.width
                    shape = RoundedCornerShape(TOP_MAX_CORNER * p)
                    clip = p > 0f
                    shadowElevation = TOP_MAX_SHADOW_DP.dp.toPx() * p
                },
        ) { content() }
    }
}
