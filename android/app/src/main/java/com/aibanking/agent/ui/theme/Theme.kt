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

// Neutral canvas with a single blue accent, in the manner of the Gemini app.
private val LightColors = lightColorScheme(
    primary = Color(0xFF0B57D0),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD3E3FD),
    onPrimaryContainer = Color(0xFF041E49),
    inversePrimary = Color(0xFFA8C7FA),
    secondary = Color(0xFF52606D),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFE9EEF6),
    onSecondaryContainer = Color(0xFF253443),
    tertiary = Color(0xFF8A5A00),
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFFEF3D6),
    onTertiaryContainer = Color(0xFF2B1A00),
    background = Color.White,
    onBackground = Color(0xFF1F1F1F),
    surface = Color.White,
    onSurface = Color(0xFF1F1F1F),
    surfaceVariant = Color(0xFFE1E3E1),
    onSurfaceVariant = Color(0xFF444746),
    surfaceTint = Color.Transparent,
    surfaceDim = Color(0xFFD8DBE0),
    surfaceBright = Color.White,
    surfaceContainerLowest = Color.White,
    surfaceContainerLow = Color(0xFFF8FAFD),
    surfaceContainer = Color(0xFFF0F4F9),
    surfaceContainerHigh = Color(0xFFE9EEF6),
    surfaceContainerHighest = Color(0xFFDDE3EA),
    outline = Color(0xFF747775),
    outlineVariant = Color(0xFFC4C7C5),
    inverseSurface = Color(0xFF303030),
    inverseOnSurface = Color(0xFFF2F2F2),
    error = Color(0xFFB3261E),
    onError = Color.White,
    errorContainer = Color(0xFFFCE8E6),
    onErrorContainer = Color(0xFF410E0B),
    scrim = Color.Black,
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFA8C7FA),
    onPrimary = Color(0xFF062E6F),
    primaryContainer = Color(0xFF0842A0),
    onPrimaryContainer = Color(0xFFD3E3FD),
    inversePrimary = Color(0xFF0B57D0),
    secondary = Color(0xFFBBC7D6),
    onSecondary = Color(0xFF243447),
    secondaryContainer = Color(0xFF303C49),
    onSecondaryContainer = Color(0xFFDEE8F3),
    tertiary = Color(0xFFFDD663),
    onTertiary = Color(0xFF3F2E00),
    tertiaryContainer = Color(0xFF3A2E10),
    onTertiaryContainer = Color(0xFFFFE7A8),
    background = Color(0xFF131314),
    onBackground = Color(0xFFE3E3E3),
    surface = Color(0xFF131314),
    onSurface = Color(0xFFE3E3E3),
    surfaceVariant = Color(0xFF444746),
    onSurfaceVariant = Color(0xFFC4C7C5),
    surfaceTint = Color.Transparent,
    surfaceDim = Color(0xFF131314),
    surfaceBright = Color(0xFF37393B),
    surfaceContainerLowest = Color(0xFF0D0E10),
    surfaceContainerLow = Color(0xFF181A1D),
    surfaceContainer = Color(0xFF202327),
    surfaceContainerHigh = Color(0xFF2B2E33),
    surfaceContainerHighest = Color(0xFF363A40),
    outline = Color(0xFF8E918F),
    outlineVariant = Color(0xFF444746),
    inverseSurface = Color(0xFFE3E3E3),
    inverseOnSurface = Color(0xFF303030),
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
    val shimmerTheme = remember {
        defaultShimmerTheme.copy(
            animationSpec = infiniteRepeatable(tween<Float>(1400, delayMillis = 250, easing = LinearEasing)),
            rotation = 12f,
            shaderColors = listOf(Color.White.copy(alpha = 0.45f), Color.White, Color.White.copy(alpha = 0.45f)),
            shimmerWidth = 240.dp,
        )
    }
    MaterialTheme(colorScheme = if (darkTheme) DarkColors else LightColors, typography = BankTypography, shapes = BankShapes) {
        CompositionLocalProvider(
            LocalBankSemanticColors provides bankSemanticColors(darkTheme),
            LocalBankMotionEnabled provides motionEnabled,
            LocalShimmerTheme provides shimmerTheme,
            content = content,
        )
    }
}
