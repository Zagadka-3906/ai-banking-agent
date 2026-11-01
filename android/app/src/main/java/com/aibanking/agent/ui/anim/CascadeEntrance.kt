package com.aibanking.agent.ui.anim

import android.os.SystemClock
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.Easing
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
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

/**
 * 级联交错入场动画规格参数（Material Design 3 风格）
 *
 * 1. 动画设计：页面首次加载或刷新完成时，列表项/卡片自下而上偏移（位移约 30dp~50dp，默认 36dp）并叠加透明度（0f -> 1f）。
 * 2. 交错节奏：每个子项之间施加固定的延迟偏移（例如 index * 40ms），形成波浪式依次推入效果；单项时长约 300ms，插值器选用 FastOutSlowInEasing。
 * 3. 性能控制：仅对进入屏幕可见区域的前 6~8 个 Item（默认 8 项）执行动画，后续滑动加载的 Item 正常显示，避免引起长列表滑动掉帧。
 */
data class CascadeEntranceSpec(
    val maxAnimatedItems: Int = 8,
    val staggerDelayMillis: Long = 40L,
    val durationMillis: Int = 300,
    val displacement: Dp = 36.dp,
    val easing: Easing = FastOutSlowInEasing,
    val windowDurationMs: Long = 650L,
) {
    companion object {
        val Default = CascadeEntranceSpec()
        val ContactCascade = CascadeEntranceSpec(
            maxAnimatedItems = 8,
            staggerDelayMillis = 40L,
            durationMillis = 300,
            displacement = 36.dp,
            easing = FastOutSlowInEasing,
            windowDurationMs = 650L,
        )
    }
}

/** A short entrance window prevents rows encountered during scrolling from replaying. */
class CascadeEntranceState(
    internal val clock: () -> Long = { SystemClock.uptimeMillis() },
) {
    internal var generation: Int by mutableIntStateOf(0)
    internal var windowOpenedAtMs: Long = Long.MIN_VALUE

    fun begin() {
        windowOpenedAtMs = clock()
        generation += 1
    }

    /**
     * 判断指定索引项在给定时间是否参与级联入场动画。
     * 仅在前 [spec.maxAnimatedItems] 项且处于入场窗口时间内时才参与动画；
     * 超过范围或窗口期之后加载的项（如滚动加载）返回 false，直接静态展示以确保滑动流畅。
     */
    fun shouldParticipate(
        index: Int,
        spec: CascadeEntranceSpec = CascadeEntranceSpec.Default,
        currentTimeMs: Long = clock(),
    ): Boolean {
        if (index !in 0 until spec.maxAnimatedItems) return false
        if (windowOpenedAtMs == Long.MIN_VALUE) return false
        val elapsed = currentTimeMs - windowOpenedAtMs
        return elapsed in 0..spec.windowDurationMs
    }
}

@Composable
fun rememberCascadeEntrance(
    ready: Boolean = true,
    vararg keys: Any?,
): CascadeEntranceState {
    val state = remember { CascadeEntranceState() }
    val inBackPreview = LocalInBackPreviewBeneath.current
    val motion = LocalBankMotionEnabled.current
    LaunchedEffect(ready, inBackPreview, motion, *keys) {
        if (ready && !inBackPreview && motion) state.begin()
    }
    return state
}

fun Modifier.cascadeItem(
    host: CascadeEntranceState,
    index: Int,
    spec: CascadeEntranceSpec = CascadeEntranceSpec.Default,
): Modifier = composed {
    if (LocalInBackPreviewBeneath.current || !LocalBankMotionEnabled.current) return@composed this
    val generation = host.generation
    val participates = remember(host, generation, index, spec) {
        host.shouldParticipate(index, spec)
    }
    val animation = remember(host, generation, index, spec) { Animatable(if (participates) 0f else 1f) }
    if (participates) {
        LaunchedEffect(host, generation, index, spec) {
            delay(index * spec.staggerDelayMillis)
            animation.animateTo(1f, tween(spec.durationMillis, easing = spec.easing))
        }
    }
    graphicsLayer {
        alpha = animation.value
        translationY = (1f - animation.value) * spec.displacement.toPx()
    }
}
