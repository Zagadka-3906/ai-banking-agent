package com.aibanking.agent.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.drawOutline
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.anim.bankPressScale
import com.aibanking.agent.ui.theme.BankPalette

/** An unblurred, offset silhouette; drawn outside the clip of the foreground. */
fun Modifier.bankHardShadow(shape: Shape, offset: Dp = BankPalette.shadowOffset): Modifier = drawBehind {
    val outline = shape.createOutline(size, layoutDirection, this)
    translate(offset.toPx(), offset.toPx()) { drawOutline(outline, BankPalette.Ink) }
}

@Composable
fun BankCard(
    modifier: Modifier = Modifier,
    shape: Shape = MaterialTheme.shapes.large,
    colors: CardColors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    elevation: CardElevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
    border: BorderStroke? = null,
    onClick: (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val frame = border ?: BorderStroke(BankPalette.borderWidth, MaterialTheme.colorScheme.outline)
    val framed = modifier.bankHardShadow(shape)
    if (onClick == null) {
        Card(modifier = framed, shape = shape, colors = colors, elevation = elevation, border = frame, content = content)
    } else {
        Card(onClick = onClick, modifier = framed, shape = shape, colors = colors, elevation = elevation, border = frame, content = content)
    }
}

@Composable
fun BankButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    shape: Shape = RoundedCornerShape(12.dp),
    colors: ButtonColors = ButtonDefaults.buttonColors(),
    contentPadding: PaddingValues = PaddingValues(horizontal = 20.dp, vertical = 12.dp),
    interactionSource: MutableInteractionSource? = null,
    content: @Composable RowScope.() -> Unit,
) {
    val interaction = interactionSource ?: remember { MutableInteractionSource() }
    Button(
        onClick = onClick, enabled = enabled,
        modifier = modifier.heightIn(min = 48.dp).bankPressScale(interaction, enabled)
            .then(if (enabled) Modifier.bankHardShadow(shape, 2.dp) else Modifier),
        shape = shape, colors = colors, contentPadding = contentPadding, interactionSource = interaction,
        border = BorderStroke(BankPalette.borderWidth, MaterialTheme.colorScheme.outline.copy(alpha = if (enabled) 1f else 0.25f)),
        content = content,
    )
}

@Composable
fun BankOutlinedButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    shape: Shape = RoundedCornerShape(12.dp),
    colors: ButtonColors = ButtonDefaults.buttonColors(
        containerColor = MaterialTheme.colorScheme.surface,
        contentColor = MaterialTheme.colorScheme.onSurface,
    ),
    contentPadding: PaddingValues = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
    content: @Composable RowScope.() -> Unit,
) = BankButton(onClick, modifier, enabled, shape, colors, contentPadding, content = content)

@Composable
fun BankTextButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    contentPadding: PaddingValues = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
    colors: ButtonColors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.secondary),
    content: @Composable RowScope.() -> Unit,
) = TextButton(onClick = onClick, modifier = modifier, enabled = enabled,
    contentPadding = contentPadding, colors = colors, content = content)

/** Shared sheet frame keeps task, device and card-entry panels in the same system. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BankBottomSheet(
    onDismissRequest: () -> Unit,
    sheetState: SheetState,
    containerColor: Color = MaterialTheme.colorScheme.background,
    content: @Composable ColumnScope.() -> Unit,
) {
    val shape = RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp)
    ModalBottomSheet(
        onDismissRequest = onDismissRequest, sheetState = sheetState,
        containerColor = containerColor, shape = shape, tonalElevation = 0.dp,
        dragHandle = null,
    ) {
        // ModalBottomSheet's modifier belongs to the dialog layout. Frame the
        // sheet content itself so a short task sheet does not outline its scrim.
        Surface(modifier = Modifier.fillMaxWidth(), shape = shape, color = containerColor,
            border = BorderStroke(BankPalette.borderWidth, MaterialTheme.colorScheme.outline)) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                BottomSheetDefaults.DragHandle()
                content()
            }
        }
    }
}

@Composable
fun BankSticker(text: String, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier.rotate(-2f), shape = RoundedCornerShape(6.dp),
        color = BankPalette.Ink, contentColor = BankPalette.Wisteria,
    ) {
        Row(Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Box(Modifier.size(6.dp).rotate(45f).background(BankPalette.Wisteria))
            Text(text, style = MaterialTheme.typography.labelMedium)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BankFilterChip(
    selected: Boolean,
    onClick: () -> Unit,
    label: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    shape: Shape = RoundedCornerShape(8.dp),
    leadingIcon: (@Composable () -> Unit)? = null,
) {
    FilterChip(
        selected = selected, onClick = onClick, label = label,
        modifier = modifier.heightIn(min = 48.dp), enabled = enabled, shape = shape, leadingIcon = leadingIcon,
        colors = FilterChipDefaults.filterChipColors(
            containerColor = MaterialTheme.colorScheme.surface, labelColor = MaterialTheme.colorScheme.onSurface,
            iconColor = MaterialTheme.colorScheme.onSurface,
            selectedContainerColor = BankPalette.Wisteria, selectedLabelColor = BankPalette.Ink,
            selectedLeadingIconColor = BankPalette.Ink,
        ),
        border = FilterChipDefaults.filterChipBorder(
            enabled = enabled, selected = selected, borderColor = MaterialTheme.colorScheme.outline,
            selectedBorderColor = MaterialTheme.colorScheme.outline,
            borderWidth = BankPalette.borderWidth, selectedBorderWidth = BankPalette.borderWidth,
        ),
    )
}
