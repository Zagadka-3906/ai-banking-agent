package com.aibanking.agent.ui.anim

import android.os.SystemClock
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

/**
 * 级联交错（Staggered/Cascade）入场：页面首次加载或刷新完成时，可见区前
 * 若干子项自下而上（40dp）+ 淡入（0→1）依次进场。
 *
 * 规格：单项 300ms + FastOutSlowInEasing，相邻子项固定延迟 40ms
 * （index * 40ms 的波浪式推入）。
 *
 * 性能护栏（写死在实现里，长列表不掉帧）：
 *  * [CASCADE_MAX_ITEMS]：只有最前面的 6~8 个子项参与动画，之后的 index
 *    直接整帧显示，滚动加载的新 Item 永远不动画；
 *  * [CascadeEntranceState.begin] 之后 [CASCADE_WINDOW_MS] 内才组合的子项
 *    才申领动画，晚于窗口（用户开始滚动、数据迟到）的一律正常显示；
 *  * 逐帧只写 graphicsLayer 的 alpha/translationY（RenderNode 合成），
 *    动画期间页面树零重组；
 *  * 预测性返回的底层预览里（[LocalInBackPreviewBeneath]）不重播——预览
 *    应呈现的是已落定的页面。
 */
class CascadeEntranceState {
    internal var generation: Int by mutableIntStateOf(0)
    internal var windowOpenedAtMs: Long = Long.MIN_VALUE

    /** 数据就绪或刷新完成：打开（或重开）一轮交错窗口。 */
    fun begin() {
        generation += 1
        windowOpenedAtMs = SystemClock.uptimeMillis()
    }
}

/**
 * 在列表所在页面创建级联状态。[ready] 为数据是否就绪：页面进入即开窗一次
 * （覆盖“数据已缓存、进入即见列表”的重进场景），数据就绪时再开一窗（覆盖
 * 首载/刷新晚到的场景——列表项组合落在这一窗内，波浪照常播放）。
 */
@Composable
fun rememberCascadeEntrance(ready: Boolean): CascadeEntranceState {
    val state = remember { CascadeEntranceState() }
    val inBackPreview = LocalInBackPreviewBeneath.current
    LaunchedEffect(Unit) { if (!inBackPreview) state.begin() }
    LaunchedEffect(ready) { if (ready && !inBackPreview) state.begin() }
    return state
}

/**
 * 列表子项的级联入场。[index] 为子项在整张列表中的扁平序号（0 起，包含
 * 分组标题等非数据项）。不参与动画的子项走恒等修饰符，零开销。
 */
fun Modifier.cascadeItem(host: CascadeEntranceState, index: Int): Modifier = composed {
    if (LocalInBackPreviewBeneath.current) return@composed this
    val participates = index in 0 until CASCADE_MAX_ITEMS &&
        host.windowOpenedAtMs > Long.MIN_VALUE &&
        SystemClock.uptimeMillis() - host.windowOpenedAtMs <= CASCADE_WINDOW_MS
    val animation = remember(host.generation, participates) {
        Animatable(if (participates) 0f else 1f)
    }
    if (participates) {
        LaunchedEffect(host.generation) {
            delay(index * CASCADE_STEP_MS)
            animation.animateTo(1f, tween(CASCADE_ITEM_MILLIS, easing = FastOutSlowInEasing))
        }
    }
    graphicsLayer {
        val a = animation.value
        alpha = a
        translationY = (1f - a) * CASCADE_RISE.toPx()
    }
}

// 参与波浪的子项数量上限（规格 6~8，取 8）。
private const val CASCADE_MAX_ITEMS = 8

// 相邻子项的固定延迟与单项规格。
private const val CASCADE_STEP_MS = 40L
private const val CASCADE_ITEM_MILLIS = 300

// 开窗后多久内组合的子项仍算“首批”，超出即视为滚动加载，不再动画。
private const val CASCADE_WINDOW_MS = 800L

// 子项自下而上的初始位移。
private val CASCADE_RISE = 40.dp
