package com.aibanking.agent.ui.anim

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

/** Shared entrance and greeting effects respect the system motion preference. */

/** Remembers which keys were already on screen, so only newly arrived items animate. */
class EntranceTracker internal constructor(initial: Set<String>) {
    private val seen = initial.toMutableSet()

    /** True the first time a key is asked about after the tracker was created. */
    fun isNew(key: String): Boolean = seen.add(key)
}

@Composable
fun rememberEntranceTracker(initialKeys: Collection<String>): EntranceTracker =
    remember { EntranceTracker(initialKeys.toSet()) }

/**
 * Fade in while rising [rise] (or sliding from [fromX]) after [delayMillis].
 * Animates only when [animate] is true; otherwise the content is drawn as is.
 */
fun Modifier.riseIn(
    animate: Boolean = true,
    delayMillis: Int = 0,
    durationMillis: Int = 320,
    rise: Dp = 14.dp,
    fromX: Dp = 0.dp,
    fromScale: Float = 1f,
): Modifier = composed {
    val motion = LocalBankMotionEnabled.current && !LocalInBackPreviewBeneath.current
    val run = animate && motion
    val progress = remember { Animatable(if (run) 0f else 1f) }
    LaunchedEffect(run) {
        if (run && progress.value < 1f) {
            if (delayMillis > 0) delay(delayMillis.toLong())
            progress.animateTo(1f, tween(durationMillis, easing = EmphasizedDecelerateEasing))
        } else progress.snapTo(1f)
    }
    graphicsLayer {
        val p = progress.value
        alpha = p
        translationY = (1f - p) * rise.toPx()
        translationX = (1f - p) * fromX.toPx()
        val s = fromScale + (1f - fromScale) * p
        scaleX = s
        scaleY = s
    }
}

/**
 * Gradient headline whose colours sweep in once from the left - the greeting
 * effect. Semantics always carry the full text.
 */
@Composable
fun GradientRevealText(text: String, colors: List<Color>, modifier: Modifier = Modifier, style: TextStyle = LocalTextStyle.current) {
    val motion = LocalBankMotionEnabled.current && !LocalInBackPreviewBeneath.current
    val reveal = remember { Animatable(if (motion) 0f else 1f) }
    LaunchedEffect(Unit) { if (motion) reveal.animateTo(1f, tween(1100, easing = FastOutSlowInEasing)) }
    val c0 = colors.first()
    val c1 = colors.getOrElse(1) { c0 }
    val c2 = colors.last()
    // The painted edge travels left → right; beyond it the glyphs are still transparent.
    val e = reveal.value * 1.2f
    val brush = if (e >= 1f) {
        Brush.horizontalGradient(listOf(c0, c1, c2))
    } else {
        val head = e.coerceIn(0.001f, 1f)
        Brush.horizontalGradient(
            0f to c0,
            (head * 0.5f) to c1,
            head to c2,
            (e + 0.2f).coerceIn(head + 0.0001f, 1f) to Color.Transparent,
            1f to Color.Transparent,
        )
    }
    Text(text, modifier, style = style.copy(brush = brush))
}
