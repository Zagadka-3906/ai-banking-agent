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
val BankCobalt = BankPalette.Wisteria
val BankCyan = BankPalette.Blue
val BankGold = BankPalette.Yellow
val BankSurfaceBg = BankPalette.Moon
val BankCardSurface = Color.White
val RiskGreen = BankPalette.Green
val RiskYellow = BankPalette.Yellow
val RiskRed = BankPalette.Red

private val LightColors = lightColorScheme(
    primary = BankPalette.Wisteria,
    onPrimary = BankPalette.Ink,
    primaryContainer = BankPalette.Lavender,
    onPrimaryContainer = BankPalette.Ink,
    inversePrimary = BankPalette.Wisteria,
    secondary = BankPalette.Ink,
    onSecondary = Color.White,
    secondaryContainer = BankPalette.Lavender,
    onSecondaryContainer = BankPalette.Ink,
    tertiary = BankPalette.Yellow,
    onTertiary = BankPalette.Ink,
    tertiaryContainer = BankPalette.YellowSoft,
    onTertiaryContainer = BankPalette.Ink,
    background = BankPalette.Moon,
    onBackground = BankPalette.Ink,
    surface = Color.White,
    onSurface = BankPalette.Ink,
    surfaceVariant = BankPalette.Lavender,
    onSurfaceVariant = BankPalette.Muted,
    surfaceTint = Color.Transparent,
    surfaceDim = BankPalette.Neutral,
    surfaceBright = Color.White,
    surfaceContainerLowest = Color.White,
    surfaceContainerLow = BankPalette.Moon,
    surfaceContainer = BankPalette.Moon,
    surfaceContainerHigh = BankPalette.Lavender,
    surfaceContainerHighest = BankPalette.Neutral,
    outline = BankPalette.Ink,
    outlineVariant = BankPalette.Line,
    inverseSurface = BankPalette.Ink,
    inverseOnSurface = Color.White,
    error = Color(0xFFB42318),
    onError = Color.White,
    errorContainer = BankPalette.RedSoft,
    onErrorContainer = BankPalette.Ink,
    scrim = BankPalette.Ink,
)

private val DarkColors = darkColorScheme(
    primary = BankPalette.Wisteria,
    onPrimary = BankPalette.Ink,
    primaryContainer = Color(0xFF3D315A),
    onPrimaryContainer = Color(0xFFF3EDFF),
    inversePrimary = BankPalette.Wisteria,
    secondary = Color(0xFFF3EDFF),
    onSecondary = BankPalette.Ink,
    secondaryContainer = Color(0xFF352D44),
    onSecondaryContainer = Color(0xFFF3EDFF),
    tertiary = BankPalette.Yellow,
    onTertiary = BankPalette.Ink,
    tertiaryContainer = Color(0xFF4F4020),
    onTertiaryContainer = BankPalette.YellowSoft,
    background = BankPalette.Ink,
    onBackground = Color(0xFFF7F6FA),
    surface = Color(0xFF25202F),
    onSurface = Color(0xFFF7F6FA),
    surfaceVariant = Color(0xFF352D44),
    onSurfaceVariant = Color(0xFFCCC7DB),
    surfaceTint = Color.Transparent,
    surfaceDim = BankPalette.Ink,
    surfaceBright = Color(0xFF403749),
    surfaceContainerLowest = Color(0xFF100D17),
    surfaceContainerLow = Color(0xFF211C2A),
    surfaceContainer = Color(0xFF25202F),
    surfaceContainerHigh = Color(0xFF30283C),
    surfaceContainerHighest = Color(0xFF3D344B),
    outline = BankPalette.Wisteria,
    outlineVariant = Color(0xFF61556F),
    inverseSurface = BankPalette.Moon,
    inverseOnSurface = BankPalette.Ink,
    error = Color(0xFFFF897D),
    onError = BankPalette.Ink,
    errorContainer = Color(0xFF572B2C),
    onErrorContainer = BankPalette.RedSoft,
    scrim = Color.Black,
)

private val BankTypography = Typography(
    displayLarge = TextStyle(fontSize = 52.sp, lineHeight = 60.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-2).sp, fontFeatureSettings = "tnum"),
    displayMedium = TextStyle(fontSize = 42.sp, lineHeight = 50.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-1.5).sp, fontFeatureSettings = "tnum"),
    displaySmall = TextStyle(fontSize = 34.sp, lineHeight = 42.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-1).sp, fontFeatureSettings = "tnum"),
    headlineLarge = TextStyle(fontSize = 30.sp, lineHeight = 38.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-0.6).sp),
    headlineMedium = TextStyle(fontSize = 28.sp, lineHeight = 36.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-0.5).sp),
    headlineSmall = TextStyle(fontSize = 24.sp, lineHeight = 32.sp, fontWeight = FontWeight.SemiBold, fontFeatureSettings = "tnum"),
    titleLarge = TextStyle(fontSize = 22.sp, lineHeight = 30.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 24.sp, fontWeight = FontWeight.SemiBold),
    titleSmall = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 26.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 22.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 18.sp),
    labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold),
    labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 18.sp, fontWeight = FontWeight.Medium),
    labelSmall = TextStyle(fontSize = 10.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.4.sp),
)

private val BankShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(12.dp),
    large = RoundedCornerShape(16.dp),
    extraLarge = RoundedCornerShape(24.dp),
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
