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

// Legacy names remain source-compatible with existing screens.
val BankNavy = Color(0xFF124C3C)
val BankCobalt = Color(0xFF176B52)
val BankCyan = Color(0xFF388269)
val BankGold = Color(0xFF8A681E)
val BankSurfaceBg = Color(0xFFF5F6F1)
val BankCardSurface = Color(0xFFFFFFFF)
val RiskGreen = Color(0xFF176B52)
val RiskYellow = Color(0xFF916618)
val RiskRed = Color(0xFFBA3D38)

private val LightColors = lightColorScheme(
    primary = BankNavy,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE0EFE5),
    onPrimaryContainer = Color(0xFF103C30),
    inversePrimary = Color(0xFFA6D9BB),
    secondary = BankCobalt,
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFE8F0E9),
    onSecondaryContainer = Color(0xFF234C3D),
    tertiary = BankGold,
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFF5EDD7),
    onTertiaryContainer = Color(0xFF584214),
    background = BankSurfaceBg,
    onBackground = Color(0xFF1C3028),
    surface = BankCardSurface,
    onSurface = Color(0xFF1C3028),
    surfaceVariant = Color(0xFFECF0E9),
    onSurfaceVariant = Color(0xFF5D6E64),
    surfaceTint = BankNavy,
    surfaceDim = Color(0xFFDEE4DA),
    surfaceBright = Color(0xFFFFFFFF),
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceContainerLow = Color(0xFFF8F9F5),
    surfaceContainer = Color(0xFFF0F3ED),
    surfaceContainerHigh = Color(0xFFE9EDE5),
    surfaceContainerHighest = Color(0xFFE2E8DF),
    outline = Color(0xFF768579),
    outlineVariant = Color(0xFFDDE5DC),
    inverseSurface = Color(0xFF24392E),
    inverseOnSurface = Color(0xFFF0F6EF),
    error = RiskRed,
    onError = Color.White,
    errorContainer = Color(0xFFFFE9E5),
    onErrorContainer = Color(0xFF7E2925),
    scrim = Color(0xFF071C13),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFA6D9BB),
    onPrimary = Color(0xFF113A29),
    primaryContainer = Color(0xFF1D4B38),
    onPrimaryContainer = Color(0xFFD4F3E0),
    inversePrimary = BankNavy,
    secondary = Color(0xFFB3CDBA),
    onSecondary = Color(0xFF243B2C),
    secondaryContainer = Color(0xFF354D3D),
    onSecondaryContainer = Color(0xFFDBEFDE),
    tertiary = Color(0xFFE3C788),
    onTertiary = Color(0xFF41320E),
    tertiaryContainer = Color(0xFF56451F),
    onTertiaryContainer = Color(0xFFF8E5B7),
    background = Color(0xFF101A15),
    onBackground = Color(0xFFE5EEE5),
    surface = Color(0xFF19261E),
    onSurface = Color(0xFFE5EEE5),
    surfaceVariant = Color(0xFF2C3B30),
    onSurfaceVariant = Color(0xFFB9C8BC),
    surfaceTint = Color(0xFFA6D9BB),
    surfaceDim = Color(0xFF101A15),
    surfaceBright = Color(0xFF35433A),
    surfaceContainerLowest = Color(0xFF0B140F),
    surfaceContainerLow = Color(0xFF15221A),
    surfaceContainer = Color(0xFF1C2A21),
    surfaceContainerHigh = Color(0xFF26342B),
    surfaceContainerHighest = Color(0xFF304035),
    outline = Color(0xFF87988A),
    outlineVariant = Color(0xFF394B3E),
    inverseSurface = Color(0xFFE0EBDF),
    inverseOnSurface = Color(0xFF1C3028),
    error = Color(0xFFFFB4AB),
    onError = Color(0xFF690005),
    errorContainer = Color(0xFF702D2A),
    onErrorContainer = Color(0xFFFFDAD5),
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
    medium = RoundedCornerShape(20.dp),
    large = RoundedCornerShape(24.dp),
    extraLarge = RoundedCornerShape(32.dp),
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
