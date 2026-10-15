package com.aibanking.agent.ui.components

import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import com.aibanking.agent.R

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
