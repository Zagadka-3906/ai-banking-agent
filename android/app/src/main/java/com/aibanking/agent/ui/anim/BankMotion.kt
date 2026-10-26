package com.aibanking.agent.ui.anim

import android.database.ContentObserver
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.HapticFeedbackConstants
import android.view.View
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.spring
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext

/** Explicit guard for custom delays, infinite shimmer and gesture transforms. */
val LocalBankMotionEnabled = staticCompositionLocalOf { true }

/** Shared pacing for conversation feedback; financial facts never interpolate. */
object BankConversationMotion {
    const val FeedbackMillis = 160
    const val DisclosureMillis = 220
    const val ReplyEntranceMillis = 280
}

@Composable
fun rememberBankMotionEnabled(): Boolean {
    val resolver = LocalContext.current.contentResolver
    fun enabled() = Settings.Global.getFloat(resolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f
    var motionEnabled by remember(resolver) { mutableStateOf(enabled()) }
    DisposableEffect(resolver) {
        val observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) { motionEnabled = enabled() }
        }
        resolver.registerContentObserver(Settings.Global.getUriFor(Settings.Global.ANIMATOR_DURATION_SCALE), false, observer)
        onDispose { resolver.unregisterContentObserver(observer) }
    }
    return motionEnabled
}

/** Shares the clickable's source: no extra gesture recognizer or duplicate click. */
fun Modifier.bankPressScale(interactionSource: MutableInteractionSource, enabled: Boolean = true): Modifier = composed {
    val pressed by interactionSource.collectIsPressedAsState()
    val motion = LocalBankMotionEnabled.current
    val scale by animateFloatAsState(
        targetValue = if (pressed && enabled && motion) 0.975f else 1f,
        animationSpec = if (motion) spring(dampingRatio = Spring.DampingRatioNoBouncy, stiffness = Spring.StiffnessMedium) else snap(),
        label = "bankPress",
    )
    graphicsLayer { scaleX = scale; scaleY = scale }
}

/**
 * Unified, restrained haptic vocabulary for financial interactions:
 * - [tick]: subtle gear/step feedback for wheel pickers, sliders, chart scrubbers, and segment taps
 * - [confirm]: crisp confirmation tap for completing a quiz option or submitting a high-value action
 * - [reject]: warning feedback when an invalid input or blocked action occurs
 */
object BankHaptics {
    fun tick(view: View) {
        val code = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            HapticFeedbackConstants.SEGMENT_FREQUENT_TICK
        } else {
            HapticFeedbackConstants.CLOCK_TICK
        }
        view.performHapticFeedback(code)
    }

    fun confirm(view: View) {
        val code = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            HapticFeedbackConstants.CONFIRM
        } else {
            HapticFeedbackConstants.KEYBOARD_TAP
        }
        view.performHapticFeedback(code)
    }

    fun reject(view: View) {
        val code = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            HapticFeedbackConstants.REJECT
        } else {
            HapticFeedbackConstants.LONG_PRESS
        }
        view.performHapticFeedback(code)
    }
}
