package com.aibanking.agent.ui.anim

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.onSizeChanged

/**
 * Holographic sheen sweep for premium bank cards:
 * A subtle semi-transparent angled band (cyan -> violet -> amber) glides
 * across the card face periodically when motion is enabled.
 */
fun Modifier.cardHolographicSheen(active: Boolean = true): Modifier = composed {
    val motion = LocalBankMotionEnabled.current && active
    if (!motion) return@composed this

    var cardWidth by remember { mutableFloatStateOf(1000f) }
    var cardHeight by remember { mutableFloatStateOf(600f) }

    val transition = rememberInfiniteTransition(label = "cardSheen")
    val phase by transition.animateFloat(
        initialValue = -0.5f,
        targetValue = 1.5f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 4600, easing = LinearEasing),
            repeatMode = RepeatMode.Restart,
        ),
        label = "sheenPhase",
    )

    onSizeChanged {
        cardWidth = it.width.toFloat().coerceAtLeast(1f)
        cardHeight = it.height.toFloat().coerceAtLeast(1f)
    }.drawWithContent {
        drawContent()
        val travel = cardWidth * 1.8f
        val x = -cardWidth * 0.4f + phase * travel
        val sheenBrush = Brush.linearGradient(
            colors = listOf(
                Color.Transparent,
                Color(0x1500E5FF), // soft cyan
                Color(0x20D0BCFF), // soft violet
                Color(0x18FFF59D), // soft gold
                Color.Transparent,
            ),
            start = Offset(x, 0f),
            end = Offset(x + cardWidth * 0.45f, cardHeight),
        )
        drawRect(brush = sheenBrush)
    }
}

/**
 * 3D card flip transform for sensitive card details.
 * When rotation reaches 90 degrees or above, the content reflects the back face.
 */
fun Modifier.card3dFlip(rotationY: Float): Modifier = composed {
    graphicsLayer {
        this.rotationY = rotationY
        cameraDistance = 12f * density
    }
}
