package com.aibanking.agent.ui.components

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.keyframes
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInParent
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch

private data class SegmentBounds(val left: Float, val width: Float)

/** One moving selection, with 10% mid-flight stretch and a full-width landing. */
@Composable
internal fun ElasticSegmentedControl(
    options: List<Pair<String, String>>, selected: String, onSelect: (String) -> Unit,
    modifier: Modifier = Modifier, tag: String,
) {
    val colors = MaterialTheme.colorScheme
    val motion = LocalBankMotionEnabled.current
    val bounds = remember { mutableStateMapOf<String, SegmentBounds>() }
    val center = remember { Animatable(0f) }
    val width = remember { Animatable(0f) }
    var placed by remember { mutableStateOf(false) }
    var referenceWidth by remember { mutableFloatStateOf(0f) }
    val destination = bounds[selected]
    LaunchedEffect(selected, destination, motion) {
        val target = destination ?: return@LaunchedEffect
        val targetCenter = target.left + target.width / 2
        if (!placed || !motion) {
            center.snapTo(targetCenter)
            width.snapTo(target.width)
            referenceWidth = target.width
            placed = true
        } else coroutineScope {
            val startWidth = width.value
            val peakWidth = maxOf(referenceWidth, target.width) * 1.10f
            referenceWidth = target.width
            launch { center.animateTo(targetCenter, tween(320, easing = FastOutSlowInEasing)) }
            launch { width.animateTo(target.width, keyframes {
                durationMillis = 320
                startWidth at 0
                peakWidth at 160 using FastOutSlowInEasing
                target.width at 320 using FastOutSlowInEasing
            }) }
        }
    }
    Box(modifier.fillMaxWidth().testTag(tag).clip(RoundedCornerShape(20.dp)).background(colors.surfaceContainerHigh)
        .horizontalScroll(rememberScrollState())) {
        Row(Modifier.padding(4.dp).selectableGroup().height(IntrinsicSize.Min).drawBehind {
            if (placed) {
                val w = width.value.coerceAtMost(size.width)
                val left = (center.value - w / 2).coerceIn(0f, (size.width - w).coerceAtLeast(0f))
                drawRoundRect(colors.surfaceContainerLowest, Offset(left, 0f), Size(w, size.height), CornerRadius(16.dp.toPx()))
            }
        }) {
            options.forEach { (key, label) ->
                Box(Modifier.widthIn(min = 64.dp).fillMaxHeight()
                    .onGloballyPositioned { bounds[key] = SegmentBounds(it.positionInParent().x, it.size.width.toFloat()) }
                    .testTag("${tag}_$key").clip(RoundedCornerShape(16.dp))
                    .selectable(selected = key == selected, role = Role.Tab,
                        interactionSource = remember { MutableInteractionSource() }, indication = null,
                        onClick = { if (key != selected) onSelect(key) })
                    .padding(horizontal = 14.dp, vertical = 12.dp), contentAlignment = Alignment.Center) {
                    Text(label, style = MaterialTheme.typography.labelMedium, maxLines = 1,
                        color = if (key == selected) colors.onSurface else colors.onSurfaceVariant)
                }
            }
        }
    }
}
