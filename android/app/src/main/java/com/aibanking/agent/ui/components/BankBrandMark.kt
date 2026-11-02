package com.aibanking.agent.ui.components

import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import com.aibanking.agent.R

/** Approved path lettering; dark mode uses the gold symbol with white letters. */
@Composable
fun BankBrandLockup(modifier: Modifier = Modifier) {
    Icon(
        painter = painterResource(R.drawable.ic_anyan_lockup),
        contentDescription = "AnYan",
        modifier = modifier.size(width = 156.dp, height = 44.dp),
        tint = Color.Unspecified,
    )
}

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

/** Legacy name retained for callers; the approved mark is solid graphite or gold. */
@Composable
fun BankGradientMark(modifier: Modifier = Modifier) {
    Icon(
        painter = painterResource(R.drawable.ic_anyan_mark),
        contentDescription = null,
        tint = MaterialTheme.colorScheme.primary,
        modifier = modifier.size(24.dp),
    )
}
