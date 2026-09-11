package com.aibanking.agent.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.ui.graphics.Color

// A deliberately sober palette: this screen shows money movements and approval
// decisions, so risk colour is reserved for the risk banner rather than used
// as decoration.
private val BankBlue = Color(0xFF0F3D5C)
private val BankBlueLight = Color(0xFF3E6C8A)
private val WarningAmber = Color(0xFF8A5A00)
private val DangerRed = Color(0xFF8C1D18)
private val SurfaceTint = Color(0xFFF5F7FA)

private val LightColors = lightColorScheme(
    primary = BankBlue,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD6E4EF),
    onPrimaryContainer = Color(0xFF06202F),
    secondary = BankBlueLight,
    background = SurfaceTint,
    surface = Color.White,
    error = DangerRed,
    tertiary = WarningAmber,
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF9CC7E4),
    onPrimary = Color(0xFF05263A),
    secondary = BankBlueLight,
    error = Color(0xFFFFB4AB),
    tertiary = Color(0xFFF2C078),
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
