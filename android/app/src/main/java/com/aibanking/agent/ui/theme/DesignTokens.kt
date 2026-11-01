package com.aibanking.agent.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * Shared palette: white canvas, cool grey containers and one solid blue accent.
 *
 * Screens should prefer [androidx.compose.material3.MaterialTheme] colours so
 * dark mode follows; these constants remain for fixed-colour surfaces (risk
 * chips, card faces) that look the same in both themes.
 */
object BankPalette {
    val Ink = Color(0xFF1F1F1F)
    val Wisteria = Color(0xFFD3E3FD)
    val Accent = Color(0xFF0B57D0)
    val Lavender = Color(0xFFE9EEF6)
    val Moon = Color(0xFFF0F4F9)
    val Muted = Color(0xFF444746)
    val Line = Color(0xFFC4C7C5)
    val Green = Color(0xFFC4EED0)
    val GreenSoft = Color(0xFFE6F4EA)
    val Yellow = Color(0xFFFEEFC3)
    val YellowSoft = Color(0xFFFEF7E0)
    val Red = Color(0xFFFAD2CF)
    val RedSoft = Color(0xFFFCE8E6)
    val Blue = Color(0xFF4285F4)
    val Neutral = Color(0xFFE3E3E3)

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
    val contentMaxWidth: Dp = 720.dp
}

@Immutable
data class BankSemanticColors(
    val inputFocus: Color,
    val positive: Color,
    val negative: Color,
    val neutral: Color,
    val riskGreen: Color,
    val riskYellow: Color,
    val riskRed: Color,
    /** Tonal chip fills for the three risk tiers, readable in both themes. */
    val riskGreenContainer: Color,
    val riskYellowContainer: Color,
    val riskRedContainer: Color,
    val onRiskContainer: Color,
    val chartPrimary: Color,
    val chartBenchmark: Color,
    val chartGrid: Color,
    val cardActive: Color,
    val cardFrozen: Color,
    val cardLost: Color,
    val cardVirtual: Color,
    val positiveGradient: Brush,
    val primaryGradient: Brush,
    /** Solid brush shared by the brand mark, greeting and thinking accents. */
    val brandGradient: Brush,
    val brandGradientColors: List<Color>,
)

// Equal stops retain text reveal/opacity animations without colour gradients.
private val BrandStops = List(3) { BankPalette.Accent }
private val DarkBrandStops = List(3) { Color(0xFFA8C7FA) }

private val LightSemanticColors = BankSemanticColors(
    inputFocus = BankPalette.Accent,
    // Chinese finance convention: red means gain, green means loss.
    positive = Color(0xFFD93025),
    negative = Color(0xFF188038),
    neutral = BankPalette.Muted,
    riskGreen = Color(0xFF146C2E),
    riskYellow = Color(0xFF8A5A00),
    riskRed = Color(0xFFB3261E),
    riskGreenContainer = Color(0xFFC4EED0),
    riskYellowContainer = Color(0xFFFFE7A8),
    riskRedContainer = Color(0xFFFAD2CF),
    onRiskContainer = Color(0xFF1F1F1F),
    chartPrimary = Color(0xFFD93025),
    chartBenchmark = Color(0xFF5C6B7A),
    chartGrid = Color(0xFFE1E3E1),
    cardActive = Color(0xFF1B2A4A),
    cardFrozen = BankPalette.YellowSoft,
    cardLost = BankPalette.RedSoft,
    cardVirtual = Color(0xFFE9EEF6),
    positiveGradient = Brush.horizontalGradient(listOf(Color(0xFFEA4335), Color(0xFFB3261E))),
    primaryGradient = SolidColor(Color(0xFFD3E3FD)),
    brandGradient = SolidColor(BankPalette.Accent),
    brandGradientColors = BrandStops,
)

private val DarkSemanticColors = BankSemanticColors(
    inputFocus = Color(0xFFA8C7FA),
    positive = Color(0xFFF28B82),
    negative = Color(0xFF81C995),
    neutral = Color(0xFFC4C7C5),
    riskGreen = Color(0xFF81C995),
    riskYellow = Color(0xFFFDD663),
    riskRed = Color(0xFFF28B82),
    riskGreenContainer = Color(0xFF0F5223),
    riskYellowContainer = Color(0xFF5C4300),
    riskRedContainer = Color(0xFF8C1D18),
    onRiskContainer = Color(0xFFF1F1F1),
    chartPrimary = Color(0xFFF28B82),
    chartBenchmark = Color(0xFFAAB7C4),
    chartGrid = Color(0xFF3C4043),
    cardActive = Color(0xFF1B2A4A),
    cardFrozen = BankPalette.YellowSoft,
    cardLost = BankPalette.RedSoft,
    cardVirtual = Color(0xFF273849),
    positiveGradient = Brush.horizontalGradient(listOf(Color(0xFFF6AEA9), Color(0xFFF28B82))),
    primaryGradient = SolidColor(Color(0xFF0842A0)),
    brandGradient = SolidColor(Color(0xFFA8C7FA)),
    brandGradientColors = DarkBrandStops,
)

val LocalBankSemanticColors = staticCompositionLocalOf { LightSemanticColors }

internal fun bankSemanticColors(darkTheme: Boolean): BankSemanticColors =
    if (darkTheme) DarkSemanticColors else LightSemanticColors
