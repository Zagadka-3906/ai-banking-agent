package com.aibanking.agent

import android.graphics.Bitmap
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import androidx.lifecycle.ViewModelProvider
import com.aibanking.agent.data.SessionStore
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.ChatViewModel
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/** Types through the real editor; the server recorder observes, never sends, turns. */
class NaturalConversationUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val arguments get() = InstrumentationRegistry.getArguments()
    private val token get() = checkNotNull(arguments.getString("controlToken"))
    private lateinit var observedModel: ChatViewModel
    private val folder get() = File(instrumentation.targetContext.getExternalFilesDir(null), "natural-conversations").apply { mkdirs() }

    @Test fun human_style_multi_turn_matrix() {
        assumeTrue("Scenario matrix runs only against the isolated build", BuildConfig.APPLICATION_ID.endsWith(".scenariotest"))
        ui.activityRule.scenario.onActivity { observedModel = ViewModelProvider(it)[ChatViewModel::class.java] }
        folder.listFiles()?.filter { it.isFile && it.name.matches(Regex("observations\\.json|[A-Z][0-9]+-[0-9]+(?:-error)?\\.png")) }
            ?.forEach { check(it.delete()) { "Cannot replace previous scenario artifact ${it.name}" } }
        val casePath = checkNotNull(arguments.getString("caseFile"))
        check(casePath.matches(Regex("/[A-Za-z0-9_./-]+")))
        val cases = JSONArray(shell("cat $casePath"))
        val filter = arguments.getString("caseIds")?.split(',')?.toSet()
        val report = JSONArray()
        for (index in 0 until cases.length()) {
            val case = cases.getJSONObject(index)
            val id = case.getString("id")
            if (filter != null && id !in filter) continue
            val item = JSONObject().put("id", id).put("direction", case.getString("direction"))
            val turns = JSONArray()
            item.put("turns", turns)
            report.put(item)
            try {
                newConversation(case)
                item.put("before", control(18800, "state"))
                val inputs = case.getJSONArray("turns")
                for (turnIndex in 0 until inputs.length()) {
                    val turn = inputs.getJSONObject(turnIndex)
                    val observation = JSONObject().put("step", turnIndex + 1).put("expected", turn.optJSONObject("expect"))
                    turns.put(observation)
                    try {
                        val before = messages()
                        val expectHttpError = turn.optJSONObject("expect")?.let { it.has("http_error") || it.has("stream_error") } == true
                        val priorRecordCount = if (expectHttpError) control(18810, "records").getJSONArray("records").length() else 0
                        val action = turn.optString("action")
                        ui.returnFromOutcome(settleMs = 500)
                        if (action.isNotEmpty()) {
                            observation.put("action", action)
                            act(action)
                        } else {
                            val input = turn.getString("input")
                            observation.put("input", input)
                            // Chunked input travels through the same edit callbacks as typing.
                            ui.onNodeWithTag(UiTags.CHAT_INPUT).performClick().performTextClearance()
                            input.chunked(3).forEach { chunk ->
                                ui.onNodeWithTag(UiTags.CHAT_INPUT).performTextInput(chunk)
                                Thread.sleep(45)
                            }
                            var accepted = false
                            repeat(3) {
                                if (!accepted) {
                                    ui.onNodeWithTag(UiTags.CHAT_SEND).assertIsEnabled().performClick()
                                    accepted = runCatching {
                                        ui.waitUntil(5_000) { observedModel.state.value.let {
                                            it.busy || it.input.isBlank() || messages().toString() != before.toString()
                                        } }
                                    }.isSuccess
                                }
                            }
                            check(accepted) { "The send button did not accept the click" }
                        }
                        if (expectHttpError) {
                            ui.waitUntil(35_000) {
                                !observedModel.state.value.busy && observedModel.state.value.error != null &&
                                    control(18810, "records").getJSONArray("records").length() > priorRecordCount
                            }
                            val shownError = checkNotNull(observedModel.state.value.error)
                            ui.waitUntil(6_000) {
                                runCatching { ui.onNodeWithText(shownError).assertIsDisplayed(); true }.getOrDefault(false)
                            }
                            ui.onNodeWithText(shownError).assertIsDisplayed()
                            observation.put("displayed_error", shownError)
                            observation.put("error_visible", true)
                            observation.put("messages_before", before)
                            observation.put("messages_after", messages())
                        } else {
                        ui.waitUntil(100_000) { !observedModel.state.value.busy && messages().toString() != before.toString() }
                        ui.waitUntil(100_000) {
                            val current = messages()
                            val last = current.optJSONObject(current.length() - 1)
                            last != null && last.optString("role") == "ASSISTANT" &&
                                last.optString("text").let { !it.startsWith("正在") && it != "等待您的确认" && it != "正在处理…" }
                        }
                        ui.waitForIdle()
                        val transcript = messages()
                        observation.put("displayed_reply", transcript.getJSONObject(transcript.length() - 1))
                        }
                        val recorded = control(18810, "records")
                        observation.put("server", recorded.optJSONArray("records")?.let { it.optJSONObject(it.length() - 1) })
                        observation.put("model_calls", recorded.getJSONArray("model_calls"))
                        observation.put("after", control(18800, "state"))
                        if (case.optBoolean("audit")) {
                            observation.put("audit", JSONObject().put("core", control(18800, "audit")).put("gateway", control(18810, "audit")))
                        }
                        if (turn.optBoolean("screenshot") || turnIndex == inputs.length() - 1) {
                            runCatching { screenshot("$id-${turnIndex + 1}") }
                                .onFailure { observation.put("screenshot_error", it.message) }
                        }
                    } catch (failure: Throwable) {
                        observation.put("ui_error", "${failure.javaClass.simpleName}: ${failure.message}")
                        runCatching { control(18800, "state") }.getOrNull()?.let { observation.put("after", it) }
                        observation.put("server_records", runCatching { control(18810, "records") }.getOrNull())
                        runCatching { screenshot("$id-${turnIndex + 1}-error") }
                        val focus = shell("dumpsys window").lineSequence().firstOrNull { it.contains("mCurrentFocus") }.orEmpty()
                        if (focus.contains("biometric", ignoreCase = true) || focus.contains("ConfirmLock")) {
                            shell("input keyevent 4")
                        }
                        break
                    }
                    File(folder, "observations.json").writeText(report.toString(2))
                }
            } catch (failure: Throwable) {
                item.put("setup_error", "${failure.javaClass.simpleName}: ${failure.message}")
            }
            File(folder, "observations.json").writeText(report.toString(2))
        }
        assertTrue("No scenario was selected", report.length() > 0)
        shell("cp ${File(folder, "observations.json").absolutePath} /data/local/tmp/anyan-natural-observations.json")
        check(shell("cat /data/local/tmp/anyan-natural-observations.json") == report.toString(2)) { "Observation export failed" }
        // Business expectations are assessed by scripts/evaluate_agent_scenarios.py,
        // retaining every mismatch rather than stopping the matrix at the first one.
    }

    private fun newConversation(case: JSONObject) {
        ui.waitUntil(20_000) {
            ui.onAllNodesWithTag(UiTags.LOGOUT).fetchSemanticsNodes().isNotEmpty() ||
                ui.onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().isNotEmpty()
        }
        if (ui.onAllNodesWithTag(UiTags.LOGOUT).fetchSemanticsNodes().isNotEmpty()) {
            ui.openFromDrawer(UiTags.LOGOUT)
        }
        ui.waitUntil(15_000) { ui.onAllNodesWithTag(UiTags.LOGIN_BUTTON).fetchSemanticsNodes().isNotEmpty() }
        control(18810, "reset", JSONObject())
        control(18800, "reset", JSONObject().put("risk_profile", case.optBoolean("risk_profile"))
            .put("two_cards", case.optBoolean("two_cards"))
            .put("recurring_transactions", case.optBoolean("recurring_transactions")))
        ui.onNodeWithTag(UiTags.LOGIN_PASSWORD).performScrollTo().performTextClearance()
        ui.onNodeWithTag(UiTags.LOGIN_PASSWORD).performTextInput("Bank@2026")
        ui.submitLoginForm()
        ui.waitUntil(25_000) { ui.onAllNodesWithTag(UiTags.CHAT_INPUT).fetchSemanticsNodes().size == 1 }
        Thread.sleep(400)
    }

    private fun act(action: String) {
        val controlTag = when (action) {
            "approve_red", "approve_red_wrong_otp" -> UiTags.APPROVAL_OTP
            "reject" -> UiTags.APPROVAL_REJECT
            else -> UiTags.APPROVAL_APPROVE
        }
        ui.onNodeWithTag(UiTags.CHAT_LIST).performScrollToNode(hasTestTag(controlTag))
        when (action) {
            "reject" -> ui.onNodeWithTag(UiTags.APPROVAL_REJECT).performScrollTo().performClick()
            "approve" -> ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().performClick()
            "approve_red", "approve_red_wrong_otp" -> {
                ui.onNodeWithTag(UiTags.APPROVAL_OTP).performScrollTo().performTextClearance()
                ui.onNodeWithTag(UiTags.APPROVAL_OTP).performTextInput(if (action == "approve_red") "888888" else "000000")
                val priorError = observedModel.state.value.error
                val priorPending = observedModel.state.value.pending
                var accepted = false
                repeat(3) {
                    if (!accepted) {
                        ui.onNodeWithTag(UiTags.APPROVAL_APPROVE).performScrollTo().performClick()
                        // Keyboard/focus animations can swallow a synthetic
                        // click. A previous error is not evidence of a new
                        // rejection: first prove the new click was accepted.
                        accepted = runCatching {
                            ui.waitUntil(5_000) { observedModel.state.value.let {
                                it.busy || it.error != priorError || it.pending != priorPending || credentialReady()
                            } }
                        }.isSuccess
                    }
                }
                check(accepted) { "The approval button did not accept the click" }
                var ready = false
                repeat(30) {
                    if (!ready) {
                        // A circuit/open or expired approval may reject the
                        // challenge before any system credential UI appears.
                        if (!observedModel.state.value.busy && observedModel.state.value.error != null && observedModel.state.value.error != priorError) return
                        val focus = shell("dumpsys window").lineSequence().firstOrNull { it.contains("mCurrentFocus") }.orEmpty()
                        if (focus.contains("ConfirmDeviceCredential") || focus.contains("ConfirmLock") || focus.contains("biometric", ignoreCase = true)) ready = true
                        else Thread.sleep(300)
                    }
                }
                check(ready) { "The system credential prompt did not appear" }
                Thread.sleep(1500)
                shell("input text 1234")
                shell("input keyevent 66")
            }
            else -> error("Unknown test action $action")
        }
    }

    private fun messages(): JSONArray = runCatching {
        JSONArray(SessionStore(instrumentation.targetContext).messages() ?: "[]")
    }.getOrElse { JSONArray() }

    private fun credentialReady(): Boolean = shell("dumpsys window").lineSequence()
        .firstOrNull { it.contains("mCurrentFocus") }.orEmpty().let {
            it.contains("ConfirmDeviceCredential") || it.contains("ConfirmLock") || it.contains("biometric", ignoreCase = true)
        }

    private fun control(port: Int, path: String, body: JSONObject? = null): JSONObject {
        val connection = URL("http://127.0.0.1:$port/__scenario/$path").openConnection() as HttpURLConnection
        connection.setRequestProperty("X-Scenario-Token", token)
        connection.connectTimeout = 10000
        connection.readTimeout = 20000
        connection.setRequestProperty("Connection", "close")
        if (body != null) {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        return try { JSONObject(connection.inputStream.bufferedReader().use { it.readText() }) } finally { connection.disconnect() }
    }

    private fun shell(command: String): String = instrumentation.uiAutomation.executeShellCommand(command)
        .use { descriptor -> java.io.FileInputStream(descriptor.fileDescriptor).bufferedReader().readText() }

    private fun screenshot(name: String) {
        // Compose captures this test surface without weakening FLAG_SECURE.
        // System credential windows intentionally remain outside screenshots.
        val bitmap = ui.onAllNodes(isRoot()).onFirst().captureToImage().asAndroidBitmap()
        File(folder, "$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
    }
}
