package com.aibanking.agent.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.ui.graphics.Color

// Modern fintech & banking design system palette:
// Deep royal navy, high-tech cobalt, secure emerald, risk alert pigments, and glassmorphic surfaces.
val BankNavy = Color(0xFF0B2545)
val BankCobalt = Color(0xFF134074)
val BankCyan = Color(0xFF0077B6)
val BankGold = Color(0xFFC69214)
val BankSurfaceBg = Color(0xFFF4F7FB)
val BankCardSurface = Color(0xFFFFFFFF)

val RiskGreen = Color(0xFF0D9488)
val RiskYellow = Color(0xFFD97706)
val RiskRed = Color(0xFFDC2626)

private val LightColors = lightColorScheme(
    primary = BankNavy,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFDCE8F5),
    onPrimaryContainer = Color(0xFF051B33),
    secondary = BankCobalt,
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFE2EDF8),
    onSecondaryContainer = Color(0xFF0A2540),
    tertiary = BankGold,
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFFEF3C7),
    onTertiaryContainer = Color(0xFF78350F),
    background = BankSurfaceBg,
    onBackground = Color(0xFF1E293B),
    surface = BankCardSurface,
    onSurface = Color(0xFF1E293B),
    surfaceVariant = Color(0xFFE9EFF7),
    onSurfaceVariant = Color(0xFF475569),
    error = RiskRed,
    onError = Color.White,
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF90CDF4),
    onPrimary = Color(0xFF062038),
    primaryContainer = Color(0xFF1A365D),
    onPrimaryContainer = Color(0xFFE2E8F0),
    secondary = Color(0xFF63B3ED),
    onSecondary = Color(0xFF0F172A),
    background = Color(0xFF0A1118),
    onBackground = Color(0xFFF1F5F9),
    surface = Color(0xFF121E2C),
    onSurface = Color(0xFFF1F5F9),
    surfaceVariant = Color(0xFF1E2D3D),
    onSurfaceVariant = Color(0xFF94A3B8),
    error = Color(0xFFFF897D),
    onError = Color(0xFF450A0A),
    tertiary = Color(0xFFFCD34D),
)

@Composable
fun BankAgentTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        content = content,
    )
}

