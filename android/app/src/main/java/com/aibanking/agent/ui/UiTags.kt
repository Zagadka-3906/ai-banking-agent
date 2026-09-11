package com.aibanking.agent.ui

/**
 * Test tags shared by the UI and the instrumented tests.
 *
 * Defined once so the two cannot drift: a renamed tag would otherwise turn a
 * UI test into a silent "node not found" failure that looks like a product bug.
 */
object UiTags {
    const val LOGIN_USER = "login_user"
    const val LOGIN_PASSWORD = "login_password"
    const val LOGIN_OTP = "login_otp"
    const val LOGIN_BUTTON = "login_button"

    const val CHAT_INPUT = "chat_input"
    const val CHAT_SEND = "chat_send"

    const val OPEN_DEVICES = "open_devices"
    const val OPEN_TASKS = "open_tasks"
    const val OPEN_CONTACTS = "open_contacts"
    const val CONTACT_SEARCH = "contact_search"
    const val CONTACT_OPEN_ADD = "contact_open_add"
    const val CONTACT_TRANSFER = "contact_transfer"
    const val DEVICE_REVOKE = "device_revoke"
    const val TASK_CANCEL = "task_cancel"
    const val CONTACT_ADD = "contact_add"
    const val CONTACT_REMOVE = "contact_remove"

    const val APPROVAL_APPROVE = "approval_approve"
    const val APPROVAL_REJECT = "approval_reject"
    const val APPROVAL_OTP = "approval_otp"
}
