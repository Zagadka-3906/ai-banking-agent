package com.aibanking.agent.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** Shared geometry used by every banking surface. */
object BankSpacing {
    val xxs: Dp = 4.dp
    val xs: Dp = 8.dp
    val sm: Dp = 12.dp
    val md: Dp = 16.dp
    val lg: Dp = 20.dp
    val xl: Dp = 24.dp
    val xxl: Dp = 32.dp
    val minTouchTarget: Dp = 48.dp
    val contentMaxWidth: Dp = 640.dp
}

@Immutable
data class BankSemanticColors(
    val positive: Color,
    val negative: Color,
    val neutral: Color,
    val riskGreen: Color,
    val riskYellow: Color,
    val riskRed: Color,
    val chartPrimary: Color,
    val chartBenchmark: Color,
    val chartGrid: Color,
    val cardActive: Color,
    val cardFrozen: Color,
    val cardLost: Color,
    val cardVirtual: Color,
    val positiveGradient: Brush,
    val primaryGradient: Brush,
)

private val LightSemanticColors = BankSemanticColors(
    // Chinese finance convention: red means gain, green means loss.
    positive = Color(0xFFD92D20),
    negative = Color(0xFF159947),
    neutral = Color(0xFF64748B),
    riskGreen = Color(0xFF087F5B),
    riskYellow = Color(0xFFB45309),
    riskRed = Color(0xFFB42318),
    chartPrimary = Color(0xFFD92D20),
    chartBenchmark = Color(0xFF2563EB),
    chartGrid = Color(0xFFE2E8F0),
    cardActive = Color(0xFF0F766E),
    cardFrozen = Color(0xFFB45309),
    cardLost = Color(0xFFB42318),
    cardVirtual = Color(0xFF4338CA),
    positiveGradient = Brush.horizontalGradient(listOf(Color(0xFFE5534B), Color(0xFFB42318))),
    primaryGradient = Brush.horizontalGradient(listOf(Color(0xFF134074), Color(0xFF0077B6))),
)

private val DarkSemanticColors = BankSemanticColors(
    positive = Color(0xFFFF8A80),
    negative = Color(0xFF63D391),
    neutral = Color(0xFFCBD5E1),
    riskGreen = Color(0xFF5ED6AE),
    riskYellow = Color(0xFFFBBF24),
    riskRed = Color(0xFFFF897D),
    chartPrimary = Color(0xFFFF8A80),
    chartBenchmark = Color(0xFF93C5FD),
    chartGrid = Color(0xFF334155),
    cardActive = Color(0xFF5EEAD4),
    cardFrozen = Color(0xFFFBBF24),
    cardLost = Color(0xFFFF897D),
    cardVirtual = Color(0xFFA5B4FC),
    positiveGradient = Brush.horizontalGradient(listOf(Color(0xFFFFA69E), Color(0xFFE57373))),
    primaryGradient = Brush.horizontalGradient(listOf(Color(0xFF477EA8), Color(0xFF2B9BCB))),
)

val LocalBankSemanticColors = staticCompositionLocalOf { LightSemanticColors }

internal fun bankSemanticColors(darkTheme: Boolean): BankSemanticColors =
    if (darkTheme) DarkSemanticColors else LightSemanticColors
