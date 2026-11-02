package com.aibanking.agent.ui.chat

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.aibanking.agent.data.ContactView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.BankConversationMotion
import com.aibanking.agent.ui.anim.BankHaptics
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.anim.bankPressScale
import com.aibanking.agent.ui.components.BankFilterChip
import com.aibanking.agent.ui.components.BankIcon
import com.aibanking.agent.ui.components.BankIconKind
import com.aibanking.agent.ui.theme.LocalBankSemanticColors

internal data class ComposerCompletion(
    val chipLabel: String,
    val fillText: String,
)

/**
 * Generates contextual prefix completions when the user begins typing a short
 * transfer or contact prefix (1–4 chars, no digits yet).
 */
internal fun composerAutocompleteSuggestions(
    input: String,
    contacts: List<ContactView>,
): List<ComposerCompletion> {
    val trimmed = input.trim()
    if (trimmed.isEmpty() || trimmed.length > 4 || trimmed.any(Char::isDigit)) return emptyList()
    if (contacts.isEmpty()) return emptyList()

    val matchedContacts = when {
        trimmed == "给" || trimmed == "转" || trimmed == "转账" || trimmed == "转账给" -> contacts.take(4)
        trimmed.startsWith("给") -> {
            val namePrefix = trimmed.removePrefix("给").trim()
            contacts.filter { it.name.startsWith(namePrefix, ignoreCase = true) }.take(4)
        }
        else -> contacts.filter { it.name.startsWith(trimmed, ignoreCase = true) && it.name != trimmed }.take(3)
    }
    return matchedContacts.map { contact ->
        ComposerCompletion(
            chipLabel = "↳ 给${contact.name}转账",
            fillText = "给${contact.name}转",
        )
    }
}

/** Text remains editable during a request; only sending and voice capture are gated. */
@Composable
internal fun InputRow(
    value: String,
    enabled: Boolean,
    voiceEnabled: Boolean,
    captureVoiceResult: () -> (String) -> Unit,
    cloudSpeechAvailable: ((Boolean) -> Unit) -> Unit,
    transcribeCloud: (ByteArray, (String) -> Unit, (String) -> Unit) -> Unit,
    onValueChange: (String) -> Unit,
    onSend: () -> Unit,
    modifier: Modifier = Modifier,
    processing: Boolean = false,
    contacts: List<ContactView> = emptyList(),
) {
    val focus = LocalFocusManager.current
    val view = LocalView.current
    val send = { if (enabled) { focus.clearFocus(); onSend() } }
    val voice = rememberVoiceInput(voiceEnabled, captureVoiceResult, cloudSpeechAvailable, transcribeCloud)
    val keyboard = LocalSoftwareKeyboardController.current
    LaunchedEffect(voice.voiceMode) {
        if (voice.voiceMode) { focus.clearFocus(); keyboard?.hide() }
    }
    var focused by remember { mutableStateOf(false) }
    val colors = MaterialTheme.colorScheme
    val motion = LocalBankMotionEnabled.current
    val outline by animateColorAsState(
        if (focused || voice.voiceMode) LocalBankSemanticColors.current.inputFocus else colors.outlineVariant,
        if (motion) tween(BankConversationMotion.FeedbackMillis) else snap(),
        label = "composerFocus",
    )
    val press = remember { MutableInteractionSource() }
    val completions = remember(value, contacts) { composerAutocompleteSuggestions(value, contacts) }

    Column(
        modifier
            .fillMaxWidth()
            .background(colors.background)
            .padding(start = 12.dp, end = 12.dp, top = 6.dp, bottom = 8.dp),
    ) {
        if (completions.isNotEmpty() && !voice.voiceMode) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState())
                    .padding(horizontal = 4.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                completions.forEach { completion ->
                    BankFilterChip(
                        selected = false,
                        onClick = {
                            BankHaptics.tick(view)
                            onValueChange(completion.fillText)
                        },
                        label = { Text(completion.chipLabel, style = MaterialTheme.typography.labelMedium) },
                        shape = RoundedCornerShape(14.dp),
                    )
                }
            }
        }
        Surface(
            shape = RoundedCornerShape(28.dp),
            color = colors.surfaceContainerLow,
            border = BorderStroke(1.dp, outline),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(Modifier.fillMaxWidth().padding(8.dp)) {
                if (voice.voiceMode) {
                    VoiceInputPanel(voice)
                } else {
                    TextField(
                        value = value,
                        onValueChange = onValueChange,
                        placeholder = { Text("问问安言", style = MaterialTheme.typography.bodyLarge) },
                        textStyle = MaterialTheme.typography.bodyLarge,
                        colors = TextFieldDefaults.colors(
                            focusedContainerColor = Color.Transparent,
                            unfocusedContainerColor = Color.Transparent,
                            focusedIndicatorColor = Color.Transparent,
                            unfocusedIndicatorColor = Color.Transparent,
                            cursorColor = LocalBankSemanticColors.current.inputFocus,
                        ),
                        modifier = Modifier
                            .fillMaxWidth()
                            .testTag(UiTags.CHAT_INPUT)
                            .onFocusChanged { focused = it.isFocused }
                            .semantics { contentDescription = "给安言的消息" },
                        maxLines = 5,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                        keyboardActions = KeyboardActions(onSend = { send() }),
                    )
                }
                if (!voice.voiceMode) Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    VoiceModeToggle(voice, voiceEnabled)
                    Text(
                        if (processing) "正在处理，可先编辑下一条" else "语音转文字后，由您核对发送",
                        Modifier.weight(1f),
                        style = MaterialTheme.typography.labelSmall,
                        color = colors.onSurfaceVariant,
                    )
                    FilledIconButton(
                        onClick = send,
                        enabled = enabled,
                        interactionSource = press,
                        colors = IconButtonDefaults.filledIconButtonColors(
                            containerColor = colors.primary,
                            contentColor = colors.onPrimary,
                            disabledContainerColor = colors.surfaceContainerHighest,
                            disabledContentColor = colors.onSurfaceVariant,
                        ),
                        modifier = Modifier
                            .size(48.dp)
                            .bankPressScale(press, enabled)
                            .testTag(UiTags.CHAT_SEND),
                    ) {
                        BankIcon(BankIconKind.ArrowUp, Modifier.size(22.dp), contentDescription = "发送")
                    }
                }
            }
        }
        Text(
            "安言可能会出错；资金操作以确认卡和银行核心回执为准。",
            style = MaterialTheme.typography.labelSmall,
            color = colors.onSurfaceVariant,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        )
    }
}
