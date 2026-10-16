package com.aibanking.agent.ui.components

import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import com.aibanking.agent.R
import com.aibanking.agent.ui.theme.LocalBankSemanticColors

/** Brand decoration beside the visible app name; business icons keep their own meaning. */
@Composable
fun BankBrandMark(modifier: Modifier = Modifier, tint: Color = LocalContentColor.current) {
    Icon(
        painter = painterResource(R.drawable.ic_anyan_mark),
        contentDescription = null,
        modifier = modifier.size(24.dp),
        tint = tint,
    )
}

/** The brand mark filled with the blue → violet → rose gradient. */
@Composable
fun BankGradientMark(modifier: Modifier = Modifier) {
    val gradient = LocalBankSemanticColors.current.brandGradient
    Icon(
        painter = painterResource(R.drawable.ic_anyan_mark),
        contentDescription = null,
        tint = Color.Black,
        modifier = modifier.size(24.dp)
            .graphicsLayer(compositingStrategy = CompositingStrategy.Offscreen)
            .drawWithContent {
                drawContent()
                drawRect(gradient, blendMode = BlendMode.SrcIn)
            },
    )
}
