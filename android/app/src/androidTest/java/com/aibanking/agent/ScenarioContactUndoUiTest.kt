package com.aibanking.agent

import android.os.SystemClock
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
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

/** Real Core records and request counts prove that undo never removes/recreates a contact. */
@BankingRegression
class ScenarioContactUndoUiTest {
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
        control(18810, "reset", JSONObject())
        control(18800, "reset", JSONObject())
        ui.ensureLoggedIn("Bank@2026")
        ui.waitUntil(20_000) { model.state.value.authenticated }
    }

    @After fun release() {
        runCatching { control(18800, "release", JSONObject().put("id", "remove-error")) }
    }

    @Test fun undo_through_the_visible_button_preserves_id_and_never_calls_remove_or_add() {
        val contact = createContact()
        openContact(contact)
        val additions = requests("/api/v1/contacts/user_zhang/book", "POST")
        ui.onNodeWithTag(UiTags.CONTACT_REMOVE).performClick()
        assertEquals(contact, book().single { it.contactId == contact.contactId })
        assertEquals(0, requests(removePath(contact)))
        ui.onNodeWithText("撤销").assertIsDisplayed().performClick()
        ui.waitUntil(8_000) { model.state.value.pendingContactDeletions.isEmpty() }
        SystemClock.sleep(CONTACT_UNDO_MILLIS + 200)
        assertEquals(contact, book().single { it.contactId == contact.contactId })
        assertEquals(0, requests(removePath(contact)))
        assertEquals(additions, requests("/api/v1/contacts/user_zhang/book", "POST"))
        assertEquals(contact, model.state.value.contacts.single { it.contactId == contact.contactId })
    }

    @Test fun expiry_sends_one_delete_after_the_window_and_refresh_cannot_resurrect_the_pending_row() {
        val contact = createContact()
        openContact(contact)
        ui.runOnIdle { model.removeContact(contact.contactId); model.refreshContacts() }
        ui.waitUntil { !model.state.value.panelBusy }
        assertFalse(model.state.value.contacts.any { it.contactId == contact.contactId })
        assertTrue(book().any { it.contactId == contact.contactId })
        assertEquals(0, requests(removePath(contact)))
        ui.waitUntil(15_000) { requests(removePath(contact)) == 1 && book().none { it.contactId == contact.contactId } }
        ui.runOnIdle { model.refreshContacts() }
        ui.waitUntil { !model.state.value.panelBusy }
        assertFalse(model.state.value.contacts.any { it.contactId == contact.contactId })
        assertEquals(1, requests(removePath(contact)))
    }

    @Test fun logout_during_the_window_cancels_deletion_for_the_old_login() {
        val contact = createContact()
        openContact(contact)
        ui.runOnIdle { model.removeContact(contact.contactId); model.logout() }
        SystemClock.sleep(CONTACT_UNDO_MILLIS + 200)
        assertEquals(0, requests(removePath(contact)))
        ui.ensureLoggedIn("Bank@2026")
        ui.waitUntil(20_000) { model.state.value.authenticated }
        assertEquals(contact, book().single { it.contactId == contact.contactId })
    }

    @Test fun activity_recreation_preserves_the_deadline_and_original_undo_action() {
        val contact = createContact()
        openContact(contact)
        ui.runOnIdle { model.removeContact(contact.contactId) }
        val deadline = model.state.value.pendingContactDeletions.getValue(contact.contactId).deadlineMillis
        ui.activityRule.scenario.recreate()
        ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
        assertEquals(deadline, model.state.value.pendingContactDeletions.getValue(contact.contactId).deadlineMillis)
        ui.onNodeWithText("撤销").assertIsDisplayed().performClick()
        SystemClock.sleep((deadline - SystemClock.elapsedRealtime()).coerceAtLeast(0) + 200)
        assertEquals(0, requests(removePath(contact)))
        assertEquals(contact, book().single { it.contactId == contact.contactId })
    }

    @Test fun an_older_undo_cannot_cancel_a_newer_contact_delete() {
        val first = createContact()
        val second = createContact()
        openContact(first)
        ui.runOnIdle {
            model.removeContact(first.contactId)
            model.removeContact(second.contactId)
            model.undoRemoveContact(first.contactId)
        }
        ui.runOnIdle { assertEquals(second.contactId, model.state.value.contactUndoId) }
        ui.waitUntil(15_000) { requests(removePath(second)) == 1 && book().none { it.contactId == second.contactId } }
        assertEquals(0, requests(removePath(first)))
        assertEquals(first, book().single { it.contactId == first.contactId })
    }

    @Test fun an_unreadable_delete_receipt_is_reconciled_by_reading_without_recreating_the_contact() {
        val contact = createContact()
        openContact(contact)
        val additions = requests("/api/v1/contacts/user_zhang/book", "POST")
        control(18800, "hold", JSONObject().put("id", "remove-error").put("path", removePath(contact))
            .put("method", "POST").put("principal", "user_zhang").put("mode", "http_500"))
        ui.runOnIdle { model.removeContact(contact.contactId) }
        ui.waitUntil(15_000) {
            val holds = control(18800, "holds").getJSONArray("holds")
            (0 until holds.length()).any { holds.getJSONObject(it).optBoolean("arrived") }
        }
        control(18800, "release", JSONObject().put("id", "remove-error"))
        ui.waitUntil(15_000) {
            val requests = control(18800, "requests").getJSONArray("requests")
            val listReads = (0 until requests.length()).count { requests.getJSONObject(it).getString("path") == "/api/v1/contacts/user_zhang/book" && requests.getJSONObject(it).getString("method") == "GET" }
            listReads >= 2 && model.state.value.contacts.none { it.contactId == contact.contactId }
        }
        assertTrue(book().none { it.contactId == contact.contactId })
        assertEquals(1, requests(removePath(contact)))
        assertEquals(additions, requests("/api/v1/contacts/user_zhang/book", "POST"))
    }

    private fun createContact(): ContactView = runBlocking {
        counter++
        repository.add("user_zhang", credential(), "撤销验收$counter", "1990000000$counter", "朋友", "2000-02-29", UUID.randomUUID().toString())
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
    private fun requests(path: String, method: String = "POST"): Int {
        val rows = control(18800, "requests").getJSONArray("requests")
        return (0 until rows.length()).count { rows.getJSONObject(it).getString("path") == path && rows.getJSONObject(it).getString("method") == method }
    }
    private fun control(port: Int, path: String, body: JSONObject? = null): JSONObject {
        val connection = URL("http://127.0.0.1:$port/__scenario/$path").openConnection() as HttpURLConnection
        connection.setRequestProperty("X-Scenario-Token", controlToken)
        connection.connectTimeout = 10_000
        connection.readTimeout = 15_000
        if (body != null) {
            connection.requestMethod = "POST"; connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        return try { JSONObject(connection.inputStream.bufferedReader().use { it.readText() }) }
        finally { connection.disconnect() }
    }
}
