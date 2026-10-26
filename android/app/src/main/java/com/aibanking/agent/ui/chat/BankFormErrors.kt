package com.aibanking.agent.ui.chat

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import com.aibanking.agent.data.GatewayException
import kotlinx.coroutines.flow.update

internal enum class BankForm { CONTACT, CARD }

/** Authentication expiry still follows the existing account/session boundary. */
internal fun ChatViewModel.applyFormFailure(failure: Exception, form: BankForm) {
    if (failure is GatewayException && failure.sessionExpired) {
        applyPanelFailure(failure)
        return
    }
    val message = failure.message ?: "暂时无法保存，请保留当前内容并重试。"
    _state.update {
        when (form) {
            BankForm.CONTACT -> it.copy(panelBusy = false, contactFormError = message)
            BankForm.CARD -> it.copy(panelBusy = false, cardFormError = message)
        }
    }
}

@Composable
internal fun BankFormError(message: String?, modifier: Modifier = Modifier) {
    if (message != null) Text(message, modifier.testTag("bank_form_error").semantics { liveRegion = LiveRegionMode.Polite },
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
}
