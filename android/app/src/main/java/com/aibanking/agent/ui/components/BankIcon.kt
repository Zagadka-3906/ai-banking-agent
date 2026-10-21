package com.aibanking.agent.ui.components

import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.unit.dp

/** A small original vector family with consistent 24-unit geometry and rounded strokes. */
enum class BankIconKind { Home, Chat, Card, Chart, People, ArrowBack, ArrowForward, Send, Add, Search, Shield, Check, Close, More, Logout, Refresh, Bank, Transfer, Wallet, Settings, Scan, Eye, Mic, Menu, Stop, ArrowUp, Task, Keyboard }

@Composable
fun BankIcon(kind: BankIconKind, modifier: Modifier = Modifier, tint: Color = LocalContentColor.current, contentDescription: String? = null) {
    val vector = remember(kind) {
        val data = when (kind) {
            BankIconKind.Home -> "M3 10 L12 3 L21 10 M5 9 V21 H10 V15 H14 V21 H19 V9"
            BankIconKind.Chat -> "M5 4 H19 Q21 4 21 6 V15 Q21 17 19 17 H9 L4 21 V17 Q3 17 3 15 V6 Q3 4 5 4 M7 9 H17 M7 13 H13"
            BankIconKind.Card -> "M5 5 H19 Q21 5 21 7 V17 Q21 19 19 19 H5 Q3 19 3 17 V7 Q3 5 5 5 M3 10 H21 M7 15 H10"
            BankIconKind.Chart -> "M4 4 V20 H21 M8 15 L12 11 L16 13 L21 6 M17 6 H21 V10"
            BankIconKind.People -> "M9 4 A3 3 0 1 1 9 10 A3 3 0 1 1 9 4 M3 21 V18 Q3 13 9 13 Q15 13 15 18 V21 M16 4 Q21 4 21 8 Q21 11 18 11 M18 14 Q22 14 22 19 V21"
            BankIconKind.ArrowBack -> "M20 12 H4 M10 5 L3 12 L10 19"
            BankIconKind.ArrowForward -> "M4 12 H20 M14 5 L21 12 L14 19"
            BankIconKind.Send -> "M3 3 L22 12 L3 21 L6 12 Z M6 12 H21"
            BankIconKind.Add -> "M12 4 V20 M4 12 H20"
            BankIconKind.Search -> "M10.5 3 A7.5 7.5 0 1 1 10.5 18 A7.5 7.5 0 1 1 10.5 3 M16 16 L22 22"
            BankIconKind.Shield -> "M12 2 L21 6 V12 Q21 18 12 22 Q3 18 3 12 V6 Z M8 12 L11 15 L16 9"
            BankIconKind.Check -> "M5 12 L10 17 L20 6"
            BankIconKind.Close -> "M5 5 L19 19 M19 5 L5 19"
            BankIconKind.More -> "M4 12 H5 M11.5 12 H12.5 M19 12 H20"
            BankIconKind.Logout -> "M10 3 H5 Q3 3 3 5 V19 Q3 21 5 21 H10 M10 12 H21 M16 7 L21 12 L16 17"
            BankIconKind.Refresh -> "M21 10 A9 9 0 0 0 5 5 L3 8 M3 3 V8 H8 M3 14 A9 9 0 0 0 19 19 L21 16 M21 21 V16 H16"
            BankIconKind.Bank -> "M2 9 L12 3 L22 9 Z M5 12 V18 M10 12 V18 M14 12 V18 M19 12 V18 M3 21 H21"
            BankIconKind.Transfer -> "M3 7 H20 M16 3 L20 7 L16 11 M21 17 H4 M8 13 L4 17 L8 21"
            BankIconKind.Wallet -> "M19 7 V4 H5 Q3 4 3 6 V19 Q3 21 5 21 H20 Q22 21 22 19 V9 Q22 7 20 7 H5 Q3 7 3 5 M22 12 H17 Q15 12 15 14 Q15 16 17 16 H22 M18 14 H18.1"
            BankIconKind.Settings -> "M12 8 A4 4 0 1 1 12 16 A4 4 0 1 1 12 8 M9 3 H15 L16 6 L19 7 L22 11 V13 L19 17 L16 18 L15 21 H9 L8 18 L5 17 L2 13 V11 L5 7 L8 6 Z"
            BankIconKind.Scan -> "M3 8 V3 H8 M16 3 H21 V8 M21 16 V21 H16 M8 21 H3 V16 M3 12 H21 M8 8 V16 M12 8 V16 M16 8 V16"
            BankIconKind.Eye -> "M2 12 Q12 0 22 12 Q12 24 2 12 Z M12 8.5 A3.5 3.5 0 1 1 12 15.5 A3.5 3.5 0 1 1 12 8.5"
            BankIconKind.Mic -> "M12 3 Q15 3 15 6 V12 Q15 15 12 15 Q9 15 9 12 V6 Q9 3 12 3 M5 11 Q5 18 12 18 Q19 18 19 11 M12 18 V21"
            BankIconKind.Menu -> "M4 7 H20 M4 12 H20 M4 17 H14"
            BankIconKind.Stop -> "M8 7 H16 Q17 7 17 8 V16 Q17 17 16 17 H8 Q7 17 7 16 V8 Q7 7 8 7"
            BankIconKind.ArrowUp -> "M12 20 V4 M5 11 L12 4 L19 11"
            BankIconKind.Task -> "M9 4 H15 V7 H9 Z M7 5 H5 V21 H19 V5 H17 M8 13 L11 16 L16 10"
            BankIconKind.Keyboard -> "M4 6 H20 Q22 6 22 8 V16 Q22 18 20 18 H4 Q2 18 2 16 V8 Q2 6 4 6 M6 10 H6.1 M10 10 H10.1 M14 10 H14.1 M18 10 H18.1 M8 14 H16"
        }
        ImageVector.Builder(kind.name, 24.dp, 24.dp, 24f, 24f).apply {
            addPath(pathData = PathParser().parsePathString(data).toNodes(), fill = null, stroke = SolidColor(Color.Black), strokeLineWidth = 1.75f, strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round)
        }.build()
    }
    Icon(vector, contentDescription, modifier.size(24.dp), tint)
}
