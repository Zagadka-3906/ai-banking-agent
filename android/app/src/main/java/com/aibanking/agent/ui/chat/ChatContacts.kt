package com.aibanking.agent.ui.chat

import com.aibanking.agent.data.ContactView
import com.aibanking.agent.data.GatewayException
import kotlinx.coroutines.CancellationException
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
    if (_state.value.contactDeletingId != null) return
    val principal = _state.value.principal
    val revision = contactsRevision
    sessionWork.launch {
        try {
            val rows = io { contacts.list(principal, credential().orEmpty()) }
            if (revision != contactsRevision) return@launch
            _state.update { it.copy(contacts = rows, panelBusy = false, panelError = null,
                contactDeleteId = it.contactDeleteId?.takeIf { id -> rows.any { row -> row.contactId == id } }) }
        } catch (failure: GatewayException) {
            check()
            if (revision != contactsRevision) return@launch
            applyPanelFailure(failure)
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            if (revision != contactsRevision) return@launch
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

/** Opening or dismissing the confirmation never changes the book. */
fun ChatViewModel.requestContactRemoval(contactId: String) {
    val current = _state.value
    if (!current.authenticated || current.panelBusy || current.contactDeletingId != null) return
    if (current.contacts.none { it.contactId == contactId }) return
    markUserActivity()
    _state.update { it.copy(contactDeleteId = contactId) }
}

fun ChatViewModel.dismissContactRemoval() = _state.update { it.copy(contactDeleteId = null) }

/** Send immediately after confirmation; remove locally only after server evidence. */
fun ChatViewModel.confirmContactRemoval() {
    val current = _state.value
    val contactId = current.contactDeleteId ?: return
    val contact = current.contacts.firstOrNull { it.contactId == contactId } ?: return
    if (!current.authenticated || current.panelBusy || current.contactDeletingId != null) return
    val principal = current.principal
    markUserActivity()
    contactsRevision++ // A GET started before this mutation cannot restore stale rows.
    _state.update {
        it.copy(contactDeleteId = null, contactDeletingId = contactId, panelBusy = true, error = null, panelError = null)
    }
    sessionWork.launch {
        try {
            io { contacts.remove(contactId, principal, credential().orEmpty()) }
            contactsRevision++
            _state.update { it.copy(contacts = it.contacts.filterNot { row -> row.contactId == contactId },
                contactsScreen = ContactsScreen.LIST, selectedContactId = null, contactDeletingId = null,
                panelBusy = false, notice = "已删除联系人 ${contact.name}") }
            refreshContacts()
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            check()
            if (failure is GatewayException && failure.sessionExpired) {
                _state.update { it.copy(contactDeletingId = null) }
                applyPanelFailure(failure)
                return@launch
            }
            // A lost/invalid receipt may follow a successful server commit.
            // Read once to reconcile; never recreate the row or resend deletion.
            try {
                val rows = io { contacts.list(principal, credential().orEmpty()) }
                contactsRevision++
                val removed = rows.none { it.contactId == contactId }
                _state.update { it.copy(contacts = rows, contactDeletingId = null, panelBusy = false,
                    contactsScreen = if (removed) ContactsScreen.LIST else it.contactsScreen,
                    selectedContactId = if (removed) null else it.selectedContactId,
                    notice = if (removed) "已删除联系人 ${contact.name}" else null,
                    error = if (removed) null else "删除未完成，联系人仍保留，请重试。") }
            } catch (readFailure: Exception) {
                if (readFailure is CancellationException) throw readFailure
                check()
                _state.update { it.copy(contactDeletingId = null, panelBusy = false,
                    error = "删除结果暂时无法确认，请刷新联系人列表核对。") }
                if (readFailure is GatewayException && readFailure.sessionExpired) applyPanelFailure(readFailure)
            }
        }
    }
}
