package com.aibanking.agent.ui.chat

import android.os.SystemClock
import com.aibanking.agent.data.ContactView
import com.aibanking.agent.data.GatewayException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

// ChatViewModel actions for the contact book panel.

// -- contact book (通讯录式全屏界面) ---------------------------------------

fun ChatViewModel.openContacts() {
    _state.update {
        it.copy(
            showContacts = true,
            showCards = false,
            showWealth = false,
            showDevices = false,
            showTasks = false,
            contactsScreen = ContactsScreen.LIST,
            panelBusy = true,
        )
    }
    refreshContacts()
}

fun ChatViewModel.closeContacts() = _state.update { it.copy(showContacts = false) }

fun ChatViewModel.showContactDetail(contactId: String) =
    _state.update { it.copy(contactsScreen = ContactsScreen.DETAIL, selectedContactId = contactId) }

fun ChatViewModel.openContactEditor() =
    _state.update { it.copy(contactsScreen = ContactsScreen.ADD, error = null, contactFormError = null) }

fun ChatViewModel.backToContactList() =
    _state.update { it.copy(contactsScreen = ContactsScreen.LIST, selectedContactId = null) }

fun ChatViewModel.onContactSearchChange(value: String) =
    _state.update { it.copy(contactSearch = value.take(50)) }

/** Selected contact for the detail page, resolved from the loaded book. */
fun ChatViewModel.selectedContact(): ContactView? =
    _state.value.contacts.firstOrNull { it.contactId == _state.value.selectedContactId }

/**
 * 从联系人详情直接发起转账：把输入框预填成“给 <姓名> 转”，
 * 金额留给用户填写——Agent 收到不完整请求会按规矩澄清，不编造金额。
 */
fun ChatViewModel.startTransferTo(name: String) {
    onInputChange("给${name}转")
    closeContacts()
}

fun ChatViewModel.refreshContacts() {
    val principal = _state.value.principal
    sessionWork.launch {
        try {
            val rows = io { contacts.list(principal, credential().orEmpty()) }
            _state.update { it.copy(contacts = rows.filterNot { row -> row.contactId in it.pendingContactDeletions || row.contactId in it.removedContactIds }, panelBusy = false, panelError = null) }
        } catch (failure: GatewayException) {
            check()
            applyPanelFailure(failure)
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            applyPanelFailure(failure)
        }
    }
}

fun ChatViewModel.onContactFormChange(name: String? = null, phone: String? = null, relationship: String? = null, birthday: String? = null) {
    _state.update { current ->
        current.copy(
            contactFormError = null,
            contactName = name ?: current.contactName,
            // The phone field keeps digits only, mirroring the server rule.
            contactPhone = phone?.filter(Char::isDigit)?.take(20) ?: current.contactPhone,
            contactRelationship = relationship ?: current.contactRelationship,
            // Birthday is typed as YYYY-MM-DD digits with dashes preserved.
            contactBirthday = birthday?.filter { it.isDigit() || it == '-' }?.take(10) ?: current.contactBirthday,
        )
    }
}

/**
 * Save one contact (name, phone, relationship, birthday).
 *
 * The core validates everything again; whatever it returns, the local form
 * is only cleared when the server actually accepted the row, so a refusal
 * never silently eats what the user typed.
 */
fun ChatViewModel.addContact() {
    val principal = _state.value.principal
    val form = _state.value
    if (!form.canAddContact || form.panelBusy) return
    // 同一次提交的所有重试共用一个幂等键（见 [CreateAttempt]）：核心据此回放
    // 响应，读超时后的重发不会建出第二个联系人。
    val attempt = CreateAttempt.of(
        contactCreateAttempt,
        listOf(form.contactName.trim(), form.contactPhone, form.contactRelationship, form.contactBirthday)
            .joinToString("\u0000"),
    )
    contactCreateAttempt = attempt
    _state.update { it.copy(panelBusy = true, error = null, contactFormError = null) }
    sessionWork.launch {
        try {
            val created = io {
                contacts.add(
                    principal = principal,
                    sessionToken = credential().orEmpty(),
                    name = form.contactName.trim(),
                    phone = form.contactPhone,
                    relationship = form.contactRelationship,
                    birthday = form.contactBirthday.takeIf { it.isNotBlank() },
                    idempotencyKey = attempt.value,
                )
            }
            // 已经建好：这次提交到此为止，下一次保存是新的意图、新的键。
            contactCreateAttempt = null
            _state.update {
                it.copy(
                    panelBusy = false,
                    contactsScreen = ContactsScreen.LIST,
                    contactName = "",
                    contactPhone = "",
                    contactBirthday = "",
                    notice = "联系人 ${created.name} 已添加，可以直接转账",
                )
            }
            refreshContacts()
        } catch (failure: GatewayException) {
            check()
            applyFormFailure(failure, BankForm.CONTACT)
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            // 与 refresh* 同样的收尾：异常逃出协程会把“保存中…”永远留在界面上，
            // 连重试的机会都没有。
            applyFormFailure(failure, BankForm.CONTACT)
        }
    }
}

/** Optimistically hide one row; no backend mutation occurs before the five-second deadline. */
internal const val CONTACT_UNDO_MILLIS = 5_000L

fun ChatViewModel.removeContact(contactId: String) {
    val current = _state.value
    if (!current.authenticated || current.panelBusy || contactId in current.pendingContactDeletions) return
    val index = current.contacts.indexOfFirst { it.contactId == contactId }
    if (index < 0) return
    val pending = PendingContactDeletion(current.contacts[index], index, SystemClock.elapsedRealtime() + CONTACT_UNDO_MILLIS)
    val principal = current.principal
    markUserActivity()
    _state.update {
        it.copy(contacts = it.contacts.filterNot { row -> row.contactId == contactId },
            contactsScreen = ContactsScreen.LIST, selectedContactId = null,
            pendingContactDeletions = it.pendingContactDeletions + (contactId to pending),
            contactUndoId = contactId)
    }
    // The login owns this timer: logging out or process exit before the deadline cancels deletion.
    sessionWork.launch {
        delay((pending.deadlineMillis - SystemClock.elapsedRealtime()).coerceAtLeast(0))
        check()
        if (_state.value.pendingContactDeletions[contactId] != pending) return@launch
        _state.update { it.copy(pendingContactDeletions = it.pendingContactDeletions - contactId,
            removedContactIds = it.removedContactIds + contactId,
            contactUndoId = it.contactUndoId.takeUnless { id -> id == contactId }) }
        try {
            io { contacts.remove(contactId, principal, credential().orEmpty()) }
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            _state.update { it.copy(removedContactIds = it.removedContactIds - contactId,
                contacts = restoreContactRow(it.contacts, pending)) }
            if (failure is GatewayException && failure.sessionExpired) applyPanelFailure(failure)
            else {
                _state.update { it.copy(error = "联系人删除结果未确认，正在刷新列表核对。") }
                refreshContacts()
            }
        }
    }
}

/** Restore the original in-memory row, including its ID; never create a replacement contact. */
fun ChatViewModel.undoRemoveContact(contactId: String) {
    val pending = _state.value.pendingContactDeletions[contactId] ?: return
    if (SystemClock.elapsedRealtime() >= pending.deadlineMillis) return
    markUserActivity()
    _state.update {
        if (it.pendingContactDeletions[contactId] != pending) it else it.copy(
            pendingContactDeletions = it.pendingContactDeletions - contactId,
            contacts = restoreContactRow(it.contacts, pending),
            notice = "已撤销删除 ${pending.contact.name}",
            contactUndoId = it.contactUndoId.takeUnless { id -> id == contactId })
    }
}

private fun restoreContactRow(rows: List<ContactView>, pending: PendingContactDeletion): List<ContactView> =
    if (rows.any { it.contactId == pending.contact.contactId }) rows else rows.toMutableList().apply {
        add(pending.index.coerceIn(0, size), pending.contact)
    }
