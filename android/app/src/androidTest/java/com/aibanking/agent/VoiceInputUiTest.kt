package com.aibanking.agent

import android.content.Context
import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.chat.*
import com.aibanking.agent.ui.theme.BankAgentTheme
import java.io.File
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Real controller and UI; only audio hardware and remote ASR are substituted. */
@BankingRegression
class VoiceInputUiTest {
    @get:Rule val ui = createAndroidComposeRule<ComponentActivity>()
    private lateinit var voice: VoiceInput
    private var draft by mutableStateOf("帮我查一下余额")
    private var deliveries = 0
    private var starts = 0
    private var cancels = 0
    private val limits = mutableListOf<() -> Unit>()
    private val responses = mutableListOf<Pair<(String) -> Unit, (String) -> Unit>>()

    @Test fun consent_ready_and_keyboard_exit_preserve_the_existing_draft() {
        render(consent = false)
        ui.onNodeWithTag("voice_input").performClick()
        ui.onNodeWithTag("voice_cloud_consent").assertExists()
        assertEquals(0, starts)
        ui.onNodeWithText("不同意").performClick()
        ui.onNodeWithTag("draft").assertTextContains("帮我查一下余额")
        ui.onNodeWithTag("voice_input").performClick()
        ui.onNodeWithTag("voice_cloud_consent").performClick()
        ui.onNodeWithTag("voice_start").assertIsDisplayed()
        ui.onNodeWithText("转成文字后，由你核对发送").assertExists()
        screenshot("voice-ready")
        ui.onNodeWithTag("voice_input").performClick()
        ui.onNodeWithTag("draft").assertTextContains("帮我查一下余额")
        assertEquals(0, starts)
        assertEquals(0, deliveries)
    }

    @Test fun cancelled_transcription_cannot_fill_the_draft_or_interrupt_a_later_capture() {
        render()
        openAndRecord()
        screenshot("voice-recording")
        ui.onNodeWithTag("voice_finish").performClick()
        ui.onNodeWithTag("voice_transcribing").assertExists()
        screenshot("voice-transcribing")
        val old = responses.single()
        ui.onNodeWithTag("voice_cancel").performClick()
        ui.onNodeWithTag("voice_start").performClick()
        ui.runOnIdle { old.first("过期识别"); old.second("过期错误"); limits.first().invoke() }
        assertEquals(VoicePhase.RECORDING, voice.phase)
        assertNull(voice.message)
        assertEquals("帮我查一下余额", draft)
        Thread.sleep(700)
        ui.onNodeWithTag("voice_finish").performClick()
        ui.runOnIdle { responses.last().first("查询本月消费"); responses.last().first("重复回调") }
        ui.onNodeWithTag("draft").assertTextContains("查询本月消费")
        assertEquals(1, deliveries)
        ui.onNodeWithTag("voice_panel").assertDoesNotExist()
    }

    @Test fun backgrounding_discards_recording_and_late_transcription() {
        render()
        openAndRecord()
        ui.activityRule.scenario.moveToState(Lifecycle.State.CREATED)
        ui.activityRule.scenario.moveToState(Lifecycle.State.RESUMED)
        ui.runOnIdle { assertEquals(VoicePhase.IDLE, voice.phase); assertEquals(1, cancels) }
        ui.onNodeWithTag("voice_start").performClick()
        Thread.sleep(700)
        ui.onNodeWithTag("voice_finish").performClick()
        val response = responses.single()
        ui.activityRule.scenario.moveToState(Lifecycle.State.CREATED)
        ui.activityRule.scenario.moveToState(Lifecycle.State.RESUMED)
        ui.runOnIdle { response.first("后台返回的内容"); response.second("后台返回的错误") }
        assertEquals("帮我查一下余额", draft)
        assertEquals(0, deliveries)
        assertNull(voice.message)
        ui.onNodeWithTag("voice_start").assertIsDisplayed()
    }

    @Test fun dark_large_text_keeps_recording_controls_accessible_and_back_cancels() {
        render(dark = true, fontScale = 1.4f)
        openAndRecord()
        ui.onNodeWithTag("voice_finish").performScrollTo().assertIsDisplayed()
        ui.onNodeWithTag("voice_cancel").assertIsDisplayed()
        screenshot("voice-dark-large-text")
        ui.activityRule.scenario.onActivity { it.onBackPressedDispatcher.onBackPressed() }
        ui.onNodeWithTag("draft").assertTextContains("帮我查一下余额")
        assertEquals(0, deliveries)
        assertEquals(1, cancels)
    }

    @Test fun the_current_audio_limit_finishes_once_and_keeps_cancellation_available() {
        render()
        openAndRecord()
        ui.runOnIdle { limits.single().invoke(); limits.single().invoke() }
        ui.onNodeWithTag("voice_transcribing").assertExists()
        ui.onNodeWithTag("voice_start").assertIsNotEnabled()
        assertEquals(1, responses.size)
        ui.onNodeWithTag("voice_cancel").performClick()
        assertEquals(VoicePhase.IDLE, voice.phase)
        assertEquals("帮我查一下余额", draft)
        assertEquals(0, deliveries)
    }

    private fun openAndRecord() {
        ui.onNodeWithTag("voice_input").performClick()
        ui.onNodeWithTag("voice_start").performClick()
        ui.onNodeWithText("正在听你说").assertIsDisplayed()
        Thread.sleep(700)
    }

    private fun render(consent: Boolean = true, dark: Boolean = false, fontScale: Float = 1f) {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        instrumentation.uiAutomation.grantRuntimePermission(BuildConfig.APPLICATION_ID, "android.permission.RECORD_AUDIO")
        instrumentation.targetContext.getSharedPreferences("voice_input", Context.MODE_PRIVATE).edit()
            .clear().putBoolean("cloud_asr_consent", consent).commit()
        ui.setContent {
            voice = rememberVoiceInput(true, captureResult = { { text -> draft = text; deliveries++ } },
                cloudAvailable = { it(true) }, transcribeCloud = { _, success, failure -> responses += success to failure },
                audioRecorderFactory = { onLimit -> limits += onLimit; object : VoiceAudioCapture {
                    override val level = .35f
                    override fun start(): Boolean { starts++; return true }
                    override fun stop() = pcmToWav(ByteArray(3200))
                    override fun cancel() { cancels++ }
                } }, onDeviceAvailable = { false })
            CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density, fontScale), LocalBankMotionEnabled provides false) {
                BankAgentTheme(darkTheme = dark) {
                    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                        Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.SpaceBetween) {
                            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                                Text("AnYan 安言", style = MaterialTheme.typography.titleLarge)
                                Text("语音转成文字后，你可以编辑再发送。", color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            if (voice.voiceMode) Surface(shape = androidx.compose.foundation.shape.RoundedCornerShape(28.dp), color = MaterialTheme.colorScheme.surfaceContainerLow) {
                                VoiceInputPanel(voice)
                            } else Column {
                                Text(draft, Modifier.testTag("draft"))
                                VoiceModeToggle(voice, true)
                            }
                        }
                    }
                }
            }
        }
    }

    private fun screenshot(name: String) {
        ui.waitForIdle()
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val folder = File(context.getExternalFilesDir(null), "voice-review").also { it.mkdirs() }
        File(folder, "$name.png").outputStream().use {
            ui.onRoot().captureToImage().asAndroidBitmap().compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
        }
        File(folder, "$name-panel.png").outputStream().use {
            ui.onNodeWithTag("voice_panel").captureToImage().asAndroidBitmap().compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
        }
    }
}
