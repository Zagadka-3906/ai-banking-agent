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
    neutral = Color(0xFF5D6E64),
    riskGreen = Color(0xFF087F5B),
    riskYellow = Color(0xFFB45309),
    riskRed = Color(0xFFB42318),
    chartPrimary = Color(0xFFD92D20),
    chartBenchmark = Color(0xFF176B52),
    chartGrid = Color(0xFFDDE5DC),
    cardActive = Color(0xFF176B52),
    cardFrozen = Color(0xFFB45309),
    cardLost = Color(0xFFB42318),
    cardVirtual = Color(0xFF596B8E),
    positiveGradient = Brush.horizontalGradient(listOf(Color(0xFFE5534B), Color(0xFFB42318))),
    primaryGradient = Brush.linearGradient(listOf(Color(0xFF124C3C), Color(0xFF287456))),
)

private val DarkSemanticColors = BankSemanticColors(
    positive = Color(0xFFFF8A80),
    negative = Color(0xFF63D391),
    neutral = Color(0xFFB9C8BC),
    riskGreen = Color(0xFF5ED6AE),
    riskYellow = Color(0xFFFBBF24),
    riskRed = Color(0xFFFF897D),
    chartPrimary = Color(0xFFFF8A80),
    chartBenchmark = Color(0xFFA6D9BB),
    chartGrid = Color(0xFF394B3E),
    cardActive = Color(0xFF5EEAD4),
    cardFrozen = Color(0xFFFBBF24),
    cardLost = Color(0xFFFF897D),
    cardVirtual = Color(0xFFA5B4FC),
    positiveGradient = Brush.horizontalGradient(listOf(Color(0xFFFFA69E), Color(0xFFE57373))),
    primaryGradient = Brush.linearGradient(listOf(Color(0xFF1D4B38), Color(0xFF2D6348))),
)

val LocalBankSemanticColors = staticCompositionLocalOf { LightSemanticColors }

internal fun bankSemanticColors(darkTheme: Boolean): BankSemanticColors =
    if (darkTheme) DarkSemanticColors else LightSemanticColors
