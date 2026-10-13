package com.aibanking.agent.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** Palette from banking-agent-ui/boards/Palette.dc.html. */
object BankPalette {
    val Ink = Color(0xFF16131F)
    val Wisteria = Color(0xFFB9A9F5)
    val Lavender = Color(0xFFEAE5FC)
    val Moon = Color(0xFFF7F6FA)
    val Muted = Color(0xFF4E4A5C)
    val Line = Color(0xFFC9C4D6)
    val Green = Color(0xFF6EDC6A)
    val GreenSoft = Color(0xFFE1F8DC)
    val Yellow = Color(0xFFFFC933)
    val YellowSoft = Color(0xFFFFF1C4)
    val Red = Color(0xFFFF5C4D)
    val RedSoft = Color(0xFFFFE4DF)
    val Blue = Color(0xFF7AA8FF)
    val Neutral = Color(0xFFE6E4EC)
    val borderWidth = 1.5.dp
    val shadowOffset = 3.dp
}

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
    neutral = BankPalette.Muted,
    riskGreen = Color(0xFF267A23),
    riskYellow = Color(0xFF855B00),
    riskRed = Color(0xFFB42318),
    chartPrimary = Color(0xFFD92D20),
    chartBenchmark = Color(0xFF7760B9),
    chartGrid = BankPalette.Line,
    cardActive = BankPalette.Ink,
    cardFrozen = BankPalette.YellowSoft,
    cardLost = BankPalette.RedSoft,
    cardVirtual = BankPalette.Lavender,
    positiveGradient = Brush.horizontalGradient(listOf(Color(0xFFE5534B), Color(0xFFB42318))),
    primaryGradient = Brush.linearGradient(listOf(BankPalette.Lavender, BankPalette.Lavender)),
)

private val DarkSemanticColors = BankSemanticColors(
    positive = Color(0xFFFF8A80),
    negative = Color(0xFF63D391),
    neutral = Color(0xFFCCC7DB),
    riskGreen = BankPalette.Green,
    riskYellow = Color(0xFFFBBF24),
    riskRed = Color(0xFFFF897D),
    chartPrimary = Color(0xFFFF8A80),
    chartBenchmark = BankPalette.Wisteria,
    chartGrid = Color(0xFF4E465E),
    cardActive = BankPalette.Ink,
    cardFrozen = BankPalette.YellowSoft,
    cardLost = BankPalette.RedSoft,
    cardVirtual = BankPalette.Lavender,
    positiveGradient = Brush.horizontalGradient(listOf(Color(0xFFFFA69E), Color(0xFFE57373))),
    primaryGradient = Brush.linearGradient(listOf(BankPalette.Lavender, BankPalette.Lavender)),
)

val LocalBankSemanticColors = staticCompositionLocalOf { LightSemanticColors }

internal fun bankSemanticColors(darkTheme: Boolean): BankSemanticColors =
    if (darkTheme) DarkSemanticColors else LightSemanticColors
