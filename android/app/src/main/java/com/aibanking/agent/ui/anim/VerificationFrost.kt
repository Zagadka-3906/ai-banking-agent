package com.aibanking.agent.ui.anim

import android.os.Build
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/**
 * 红色风险验证的磨砂玻璃背景（Frosted Glass）：验证弹层/卡片出现时，底层
 * 内容的高斯模糊半径由 0 连续过渡到 24dp，并叠加 18% 半透明白蒙层，形成
 * 聚焦验证内容的磨砂光感；验证消失时反向收敛回 0。
 *
 * 兼容与性能：
 *  * API 31+：[Modifier.blur] 底层即 RenderEffect.createBlurEffect 的
 *    RenderNode 硬件加速路径，实时模糊不掉帧；
 *  * API <31：blur 是空实现，按规格回退为**仅蒙层**（半透明压暗/提亮），
 *    低端设备不付模糊的 GPU 成本；
 *  * 非验证态（进度归零后）返回恒等修饰符，零开销；过渡只有 350ms，期间
 *    的少量重组只发生在挂接节点上。
 *
 * 用法：套在验证弹层**之下**的背景内容上（逐块套用即可把弹层本身排除在
 * 模糊之外，保持其清晰聚焦）。
 */
const val VERIFICATION_FROST_MILLIS = 350

// 磨砂参数：满进度模糊半径与蒙层透明度（规格 20~30dp、15%~20% 取中）。
private val FROST_BLUR_RADIUS = 24.dp
private const val FROST_SCRIM_ALPHA = 0.18f

@Composable
fun Modifier.verificationFrost(active: Boolean): Modifier {
    val progress = remember { Animatable(if (active) 1f else 0f) }
    LaunchedEffect(active) {
        progress.animateTo(
            if (active) 1f else 0f,
            tween(VERIFICATION_FROST_MILLIS, easing = FastOutSlowInEasing),
        )
    }
    // API <31 的 Modifier.blur 为空实现：只保留蒙层（回退路径），不空挂模糊。
    val blurSupported = Build.VERSION.SDK_INT >= 31
    if (!active && progress.value == 0f) return this
    return then(if (blurSupported) Modifier.blur(FROST_BLUR_RADIUS * progress.value) else Modifier)
        .drawWithContent {
            drawContent()
            drawRect(Color.White.copy(alpha = FROST_SCRIM_ALPHA * progress.value))
        }
}
