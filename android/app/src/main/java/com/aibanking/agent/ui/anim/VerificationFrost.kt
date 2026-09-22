package com.aibanking.agent.ui.anim

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent

/** Legacy name retained: verification now uses a subtle scrim, without GPU-heavy blur. */
const val VERIFICATION_FROST_MILLIS = 180

@Composable
fun Modifier.verificationFrost(active: Boolean): Modifier {
    val motion = LocalBankMotionEnabled.current
    val progress by animateFloatAsState(
        targetValue = if (active) 1f else 0f,
        animationSpec = if (motion) tween(VERIFICATION_FROST_MILLIS) else snap(),
        label = "verificationFocus",
    )
    val scrim = MaterialTheme.colorScheme.scrim
    return drawWithContent {
        drawContent()
        if (progress > 0f) drawRect(scrim.copy(alpha = 0.12f * progress))
    }
}
