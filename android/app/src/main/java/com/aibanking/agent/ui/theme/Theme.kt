package com.aibanking.agent.ui.theme

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.anim.bankShimmerTheme
import com.aibanking.agent.ui.anim.rememberBankMotionEnabled
import com.valentinilk.shimmer.LocalShimmerTheme
import com.valentinilk.shimmer.defaultShimmerTheme

// Retain these names for screens that already consume the shared theme.
val BankNavy = BankPalette.Ink
val BankCobalt = BankPalette.Accent
val BankCyan = BankPalette.Blue
val BankGold = BankPalette.Yellow
val BankSurfaceBg = BankPalette.Moon
val BankCardSurface = Color.White
val RiskGreen = BankPalette.Green
val RiskYellow = BankPalette.Yellow
val RiskRed = BankPalette.Red

// Approved AnYan identity: graphite on paper; gold on dark surfaces.
private val LightColors = lightColorScheme(
    primary = Color(0xFF30312D),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE9E2D4),
    onPrimaryContainer = Color(0xFF30312D),
    inversePrimary = Color(0xFFB39A68),
    secondary = Color(0xFF665C48),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFEDE9DF),
    onSecondaryContainer = Color(0xFF30312D),
    tertiary = Color(0xFF6A5938),
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFF0E5CB),
    onTertiaryContainer = Color(0xFF302712),
    background = BankPalette.Paper,
    onBackground = Color(0xFF30312D),
    surface = BankPalette.Paper,
    onSurface = Color(0xFF30312D),
    surfaceVariant = Color(0xFFE3E0D6),
    onSurfaceVariant = Color(0xFF5E6057),
    surfaceTint = Color.Transparent,
    surfaceDim = Color(0xFFDEDCD2),
    surfaceBright = Color.White,
    surfaceContainerLowest = Color.White,
    surfaceContainerLow = Color(0xFFF9F7F1),
    surfaceContainer = Color(0xFFF1EEE5),
    surfaceContainerHigh = Color(0xFFEDE9DF),
    surfaceContainerHighest = Color(0xFFE5E1D7),
    outline = Color(0xFF77786D),
    outlineVariant = Color(0xFFCAC8BD),
    inverseSurface = Color(0xFF30312D),
    inverseOnSurface = Color(0xFFF6F3EB),
    error = Color(0xFFB3261E),
    onError = Color.White,
    errorContainer = Color(0xFFFCE8E6),
    onErrorContainer = Color(0xFF410E0B),
    scrim = Color.Black,
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFB39A68),
    onPrimary = Color(0xFF30312D),
    primaryContainer = Color(0xFF414239),
    onPrimaryContainer = Color(0xFFE9E2D4),
    inversePrimary = Color(0xFF30312D),
    secondary = Color(0xFFD2C5A8),
    onSecondary = Color(0xFF363023),
    secondaryContainer = Color(0xFF494335),
    onSecondaryContainer = Color(0xFFEDE2C9),
    tertiary = Color(0xFFFDD663),
    onTertiary = Color(0xFF3F2E00),
    tertiaryContainer = Color(0xFF3A2E10),
    onTertiaryContainer = Color(0xFFFFE7A8),
    background = Color(0xFF22231F),
    onBackground = Color(0xFFF6F3EB),
    surface = Color(0xFF22231F),
    onSurface = Color(0xFFF6F3EB),
    surfaceVariant = Color(0xFF5E6057),
    onSurfaceVariant = Color(0xFFCAC8BD),
    surfaceTint = Color.Transparent,
    surfaceDim = Color(0xFF22231F),
    surfaceBright = Color(0xFF3C3D37),
    surfaceContainerLowest = Color(0xFF191A17),
    surfaceContainerLow = Color(0xFF272822),
    surfaceContainer = Color(0xFF2C2D27),
    surfaceContainerHigh = Color(0xFF34352E),
    surfaceContainerHighest = Color(0xFF3D3E36),
    outline = Color(0xFF949589),
    outlineVariant = Color(0xFF5E6057),
    inverseSurface = Color(0xFFF6F3EB),
    inverseOnSurface = Color(0xFF30312D),
    error = Color(0xFFF2B8B5),
    onError = Color(0xFF601410),
    errorContainer = Color(0xFF3B1513),
    onErrorContainer = Color(0xFFF9DEDC),
    scrim = Color.Black,
)

// Airy type: regular-weight body, medium headings, no negative tracking on CJK, tabular figures globally.
private val BankTypography = Typography(
    displayLarge = TextStyle(fontSize = 48.sp, lineHeight = 56.sp, fontWeight = FontWeight.Normal, fontFeatureSettings = "tnum"),
    displayMedium = TextStyle(fontSize = 40.sp, lineHeight = 48.sp, fontWeight = FontWeight.Normal, fontFeatureSettings = "tnum"),
    displaySmall = TextStyle(fontSize = 32.sp, lineHeight = 40.sp, fontWeight = FontWeight.Normal, fontFeatureSettings = "tnum"),
    headlineLarge = TextStyle(fontSize = 30.sp, lineHeight = 38.sp, fontWeight = FontWeight.Medium, fontFeatureSettings = "tnum"),
    headlineMedium = TextStyle(fontSize = 26.sp, lineHeight = 34.sp, fontWeight = FontWeight.Medium, fontFeatureSettings = "tnum"),
    headlineSmall = TextStyle(fontSize = 22.sp, lineHeight = 30.sp, fontWeight = FontWeight.Medium, fontFeatureSettings = "tnum"),
    titleLarge = TextStyle(fontSize = 20.sp, lineHeight = 28.sp, fontWeight = FontWeight.Medium, fontFeatureSettings = "tnum"),
    titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 24.sp, fontWeight = FontWeight.Medium, fontFeatureSettings = "tnum"),
    titleSmall = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.Medium, fontFeatureSettings = "tnum"),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 26.sp, fontFeatureSettings = "tnum"),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 22.sp, fontFeatureSettings = "tnum"),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 18.sp, fontFeatureSettings = "tnum"),
    labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.Medium, fontFeatureSettings = "tnum"),
    labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 18.sp, fontWeight = FontWeight.Medium, fontFeatureSettings = "tnum"),
    labelSmall = TextStyle(fontSize = 11.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.2.sp, fontFeatureSettings = "tnum"),
)

/** Explicit helper to enforce OpenType tabular numerals on custom TextStyle instances. */
fun TextStyle.tabular(): TextStyle = copy(fontFeatureSettings = "tnum")

// Generous radii: cards 24 dp, sheets and the composer 28 dp, buttons fully round.
private val BankShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(24.dp),
    extraLarge = RoundedCornerShape(28.dp),
)

@Composable
fun BankAgentTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val motionEnabled = rememberBankMotionEnabled()
    val shimmerTheme = remember(darkTheme) { bankShimmerTheme(darkTheme) }
    MaterialTheme(colorScheme = if (darkTheme) DarkColors else LightColors, typography = BankTypography, shapes = BankShapes) {
        CompositionLocalProvider(
            LocalBankSemanticColors provides bankSemanticColors(darkTheme),
            LocalBankMotionEnabled provides motionEnabled,
            LocalShimmerTheme provides shimmerTheme,
            content = content,
        )
    }
}
