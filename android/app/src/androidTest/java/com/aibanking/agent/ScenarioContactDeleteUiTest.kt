package com.aibanking.agent

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.espresso.Espresso
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.data.ContactRepository
import com.aibanking.agent.data.ContactView
import com.aibanking.agent.data.SessionStore
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.*
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.*
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

/** Real server records prove that confirmation, not a local timer, owns deletion. */
@BankingRegression
class ScenarioContactDeleteUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val controlToken get() = checkNotNull(InstrumentationRegistry.getArguments().getString("controlToken"))
    private val repository = ContactRepository.create()
    private lateinit var model: ChatViewModel
    private var counter = 0

    @Before fun reset() {
        assumeTrue(BuildConfig.APPLICATION_ID.endsWith(".scenariotest"))
        ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
        ui.waitUntil(20_000) { !model.state.value.restoring }
        if (model.state.value.authenticated) ui.runOnIdle { model.logout() }
        ui.waitUntil(20_000) { !model.state.value.authenticated && !model.state.value.busy }
        control(18810, "reset", JSONObject()); control(18800, "reset", JSONObject())
        ui.ensureLoggedIn("Bank@2026")
        ui.waitUntil(20_000) { model.state.value.authenticated }
    }

    @After fun release() { runCatching { control(18800, "release", JSONObject().put("id", "remove-error")) } }

    @Test fun cancelling_the_dialog_does_not_change_the_client_or_server_book() {
        val contact = createContact(); openContact(contact)
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE).performClick()
        ui.onNodeWithText("删除联系人？").assertIsDisplayed()
        assertEquals(contact, book().single { it.contactId == contact.contactId })
        assertEquals(0, requests(removePath(contact)))
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE_CANCEL).performClick()
        ui.onNodeWithText("删除联系人？").assertDoesNotExist()
        assertEquals(contact, model.state.value.contacts.single { it.contactId == contact.contactId })
        assertEquals(0, requests(removePath(contact)))
    }

    @Test fun confirming_deletes_from_the_server_then_stays_deleted_after_refresh_and_login() {
        val contact = createContact(); openContact(contact)
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE).performClick()
        assertEquals(0, requests(removePath(contact)))
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE_CONFIRM).performClick()
        waitDeleted(contact)
        ui.onNodeWithText("撤销").assertDoesNotExist()
        ui.runOnIdle { model.refreshContacts() }
        ui.waitUntil { !model.state.value.panelBusy }
        assertFalse(model.state.value.contacts.any { it.contactId == contact.contactId })
        ui.runOnIdle { model.logout() }
        ui.ensureLoggedIn("Bank@2026")
        ui.waitUntil(20_000) { model.state.value.authenticated }
        assertFalse(book().any { it.contactId == contact.contactId })
        assertEquals(1, requests(removePath(contact)))
    }

    @Test fun dismissing_by_back_does_not_submit_a_delete() {
        val contact = createContact(); openContact(contact)
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE).performClick()
        ui.onNodeWithText("删除联系人？").assertIsDisplayed()
        Espresso.pressBack()
        ui.onNodeWithText("删除联系人？").assertDoesNotExist()
        assertEquals(contact, book().single { it.contactId == contact.contactId })
        assertEquals(0, requests(removePath(contact)))
    }

    @Test fun logging_out_before_confirmation_keeps_the_server_contact() {
        val contact = createContact(); openContact(contact)
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE).performClick()
        ui.runOnIdle { model.logout() }
        ui.ensureLoggedIn("Bank@2026")
        ui.waitUntil(20_000) { model.state.value.authenticated }
        assertEquals(contact, book().single { it.contactId == contact.contactId })
        assertEquals(0, requests(removePath(contact)))
        assertNull(model.state.value.contactDeleteId)
    }

    @Test fun activity_recreation_keeps_the_original_confirmation_without_submitting() {
        val contact = createContact(); openContact(contact)
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE).performClick()
        ui.activityRule.scenario.recreate()
        ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
        ui.onNodeWithText("删除联系人？").assertIsDisplayed()
        assertEquals(0, requests(removePath(contact)))
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE_CONFIRM).performClick()
        waitDeleted(contact)
    }

    @Test fun repeated_confirmation_sends_only_one_delete() {
        val contact = createContact(); openContact(contact)
        ui.runOnIdle {
            model.requestContactRemoval(contact.contactId)
            model.confirmContactRemoval(); model.confirmContactRemoval()
            model.refreshContacts()
        }
        waitDeleted(contact)
        assertEquals(1, requests(removePath(contact)))
    }

    @Test fun a_lost_delete_receipt_is_reconciled_without_recreating_the_contact() {
        val contact = createContact(); openContact(contact)
        val additions = requests("/api/v1/contacts/user_zhang/book")
        control(18800, "hold", JSONObject().put("id", "remove-error").put("path", removePath(contact))
            .put("method", "POST").put("principal", "user_zhang").put("mode", "http_500"))
        ui.runOnIdle { model.requestContactRemoval(contact.contactId); model.confirmContactRemoval() }
        ui.waitUntil(15_000) {
            val holds = control(18800, "holds").getJSONArray("holds")
            (0 until holds.length()).any { holds.getJSONObject(it).optBoolean("arrived") }
        }
        // Until the receipt/reconciliation completes, the client does not hide it.
        assertTrue(model.state.value.contacts.any { it.contactId == contact.contactId })
        control(18800, "release", JSONObject().put("id", "remove-error"))
        waitDeleted(contact)
        assertEquals(additions, requests("/api/v1/contacts/user_zhang/book"))
    }

    private fun waitDeleted(contact: ContactView) {
        ui.waitUntil(20_000) { model.state.value.contactDeletingId == null && model.state.value.contacts.none { it.contactId == contact.contactId } }
        assertFalse(book().any { it.contactId == contact.contactId })
        assertEquals(1, requests(removePath(contact)))
    }
    private fun createContact(): ContactView = runBlocking {
        counter++
        repository.add("user_zhang", credential(), "删除验收$counter", "1990000000$counter", "朋友", "2000-02-29", UUID.randomUUID().toString())
    }
    private fun openContact(contact: ContactView) {
        ui.runOnIdle { model.openContacts() }
        ui.waitUntil(20_000) { !model.state.value.panelBusy && model.state.value.contacts.any { it.contactId == contact.contactId } }
        ui.runOnIdle { model.showContactDetail(contact.contactId) }
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE).assertIsDisplayed()
    }
    private fun credential() = checkNotNull(SessionStore(instrumentation.targetContext).sessionToken())
    private fun book() = runBlocking { repository.list("user_zhang", credential()) }
    private fun removePath(contact: ContactView) = "/api/v1/contacts/user_zhang/book/${contact.contactId}/remove"
    private fun requests(path: String): Int {
        val rows = control(18800, "requests").getJSONArray("requests")
        return (0 until rows.length()).count { rows.getJSONObject(it).getString("path") == path && rows.getJSONObject(it).getString("method") == "POST" }
    }
    private fun control(port: Int, path: String, body: JSONObject? = null): JSONObject {
        val connection = URL("http://127.0.0.1:$port/__scenario/$path").openConnection() as HttpURLConnection
        connection.setRequestProperty("X-Scenario-Token", controlToken)
        connection.connectTimeout = 10_000; connection.readTimeout = 15_000
        if (body != null) {
            connection.requestMethod = "POST"; connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        return try { JSONObject(connection.inputStream.bufferedReader().use { it.readText() }) } finally { connection.disconnect() }
    }
}
