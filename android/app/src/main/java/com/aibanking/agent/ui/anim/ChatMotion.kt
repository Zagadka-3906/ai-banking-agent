package com.aibanking.agent.ui.anim

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TileMode
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.text
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

/*
 * Conversational motion in the manner of the Gemini app: content rises softly
 * into place, the brand gradient breathes while work is in progress, numbers
 * count up once. Every effect is skipped when the system "remove animations"
 * setting is on (LocalBankMotionEnabled) or while drawn beneath a back preview.
 */

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

/** A gentle scale pop when [trigger] changes, e.g. a send button becoming enabled. */
fun Modifier.popOnChange(trigger: Any?): Modifier = composed {
    val motion = LocalBankMotionEnabled.current
    val scale = remember { Animatable(1f) }
    var first by remember { mutableStateOf(true) }
    LaunchedEffect(trigger, motion) {
        if (!motion) { scale.snapTo(1f); first = true; return@LaunchedEffect }
        if (first) { first = false; return@LaunchedEffect }
        scale.snapTo(0.82f)
        scale.animateTo(1f, spring(dampingRatio = 0.55f, stiffness = Spring.StiffnessMedium))
    }
    graphicsLayer { scaleX = scale.value; scaleY = scale.value }
}

/** Slow, endless pulse of opacity between [min] and 1 - for "waiting on you" accents. */
fun Modifier.breathe(active: Boolean = true, min: Float = 0.55f, periodMillis: Int = 1400): Modifier = composed {
    if (!active || !LocalBankMotionEnabled.current) return@composed this
    val transition = rememberInfiniteTransition(label = "breathe")
    val alpha by transition.animateFloat(1f, min, infiniteRepeatable(tween(periodMillis, easing = FastOutSlowInEasing), RepeatMode.Reverse), label = "breatheAlpha")
    graphicsLayer { this.alpha = alpha }
}

/** Expanding rings behind a control, used while the microphone is recording. */
fun Modifier.pulseRings(active: Boolean, color: Color): Modifier = composed {
    if (!active || !LocalBankMotionEnabled.current) return@composed this
    val transition = rememberInfiniteTransition(label = "pulse")
    val phase by transition.animateFloat(0f, 1f, infiniteRepeatable(tween(1300, easing = LinearEasing)), label = "pulsePhase")
    drawBehind {
        val base = size.minDimension / 2f
        for (ring in 0..1) {
            val p = (phase + ring * 0.5f) % 1f
            drawCircle(color.copy(alpha = (1f - p) * 0.45f), radius = base * (0.9f + p * 0.75f), style = Stroke(width = 2.dp.toPx()))
        }
    }
}

/**
 * Text painted with a moving gradient, like a model "thinking" label. With
 * motion off it is painted with the static gradient.
 */
@Composable
fun ShimmerGradientText(text: String, colors: List<Color>, modifier: Modifier = Modifier, style: TextStyle = LocalTextStyle.current) {
    val motion = LocalBankMotionEnabled.current
    var width by remember { mutableStateOf(1f) }
    val shift = if (motion) {
        val transition = rememberInfiniteTransition(label = "shimmerText")
        transition.animateFloat(0f, 1f, infiniteRepeatable(tween(1600, easing = LinearEasing)), label = "shimmerShift").value
    } else 0f
    val brush = Brush.linearGradient(
        colors = colors + colors.first(),
        start = Offset(-width + shift * width * 2, 0f),
        end = Offset(shift * width * 2, 0f),
        tileMode = TileMode.Mirror,
    )
    Text(text, modifier.onSizeChanged { width = it.width.toFloat().coerceAtLeast(1f) }, style = style.copy(brush = brush))
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

/**
 * Whether numbers in this subtree may count up. Only a reply that has just
 * arrived allows it, so returning to the chat or scrolling back shows the
 * final amounts at once.
 */
val LocalCountUpAllowed = staticCompositionLocalOf { false }

/** Numbers already counted in this app run, by caller-chosen key. */
object CountUpMemory {
    private val counted = HashSet<String>()

    fun firstTime(key: String): Boolean = synchronized(counted) { counted.add(key) }
}

/**
 * Shows [format] of a number counting up from zero once, while semantics keep
 * the final text so assistive technology and tests read the real value.
 */
@Composable
fun CountUpText(
    value: Double,
    format: (Double) -> String,
    modifier: Modifier = Modifier,
    style: TextStyle = LocalTextStyle.current,
    color: Color = Color.Unspecified,
    durationMillis: Int = 700,
    onceKey: String? = null,
) {
    // Count only the first time a number is seen: with [onceKey], once per key and
    // value for this app run; otherwise only where a newly arrived reply allows it.
    val firstTime = remember(onceKey, value) { onceKey == null || CountUpMemory.firstTime("$onceKey|$value") }
    val allowed = if (onceKey != null) firstTime else LocalCountUpAllowed.current
    val motion = allowed && LocalBankMotionEnabled.current && !LocalInBackPreviewBeneath.current && value.isFinite()
    val progress = remember(value) { Animatable(if (motion) 0f else 1f) }
    LaunchedEffect(value) { if (motion) progress.animateTo(1f, tween(durationMillis, easing = FastOutSlowInEasing)) }
    val finalText = format(value)
    val shown = if (progress.value >= 1f) finalText else format(value * progress.value)
    Text(
        shown,
        modifier.clearAndSetSemantics { text = AnnotatedString(finalText) },
        style = style.copy(fontFeatureSettings = "tnum"),
        color = color,
    )
}

/**
 * Tabular rolling ticker text for monetary figures.
 * When motion is allowed, digits roll smoothly into position.
 * Assistive tech and tests read the full exact string from semantics.
 */
@Composable
fun RollingTickerText(
    value: Double,
    format: (Double) -> String,
    modifier: Modifier = Modifier,
    style: TextStyle = LocalTextStyle.current,
    color: Color = Color.Unspecified,
    durationMillis: Int = 680,
    onceKey: String? = null,
) {
    val firstTime = remember(onceKey, value) { onceKey == null || CountUpMemory.firstTime("ticker|$onceKey|$value") }
    val allowed = if (onceKey != null) firstTime else LocalCountUpAllowed.current
    val motion = allowed && LocalBankMotionEnabled.current && !LocalInBackPreviewBeneath.current && value.isFinite()
    val progress = remember(value) { Animatable(if (motion) 0f else 1f) }
    LaunchedEffect(value) {
        if (motion) progress.animateTo(1f, tween(durationMillis, easing = FastOutSlowInEasing))
    }
    val finalText = format(value)
    val shown = if (progress.value >= 1f) finalText else format(value * progress.value)
    Text(
        shown,
        modifier.clearAndSetSemantics { text = AnnotatedString(finalText) },
        style = style.copy(fontFeatureSettings = "tnum"),
        color = color,
    )
}
