package com.aibanking.agent

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.aibanking.agent.ui.auth.LocalNetworkAccessGate
import com.aibanking.agent.ui.theme.BankAgentTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
@BankingRegression
class LocalNetworkAccessUiTest {
    @get:Rule val ui = createComposeRule()

    @Test fun denial_does_not_start_content_and_retry_starts_it_only_after_permission_is_granted() {
        val permission = PermissionFixture()
        render(permission)
        ui.onNodeWithText("允许连接本地服务").assertIsDisplayed()
        ui.runOnIdle {
            assertEquals(0, permission.requests)
            assertEquals(0, permission.contentStarts)
        }
        ui.onNodeWithText("允许连接本地服务").performClick()
        ui.runOnIdle { permission.completeRequest(granted = false, rationale = true) }
        ui.onNodeWithText("应用内容").assertDoesNotExist()
        ui.onNodeWithText("重新授权").performClick()
        ui.runOnIdle {
            assertEquals(2, permission.requests)
            assertEquals(0, permission.contentStarts)
            permission.completeRequest(granted = true, rationale = false)
        }
        ui.onNodeWithText("应用内容").assertIsDisplayed()
        ui.runOnIdle { assertEquals(1, permission.contentStarts) }
    }

    @Test fun permanent_denial_opens_settings_and_grant_is_checked_again_on_resume() {
        val permission = PermissionFixture().apply { requested = true }
        render(permission)
        ui.onNodeWithText("前往设置").performClick()
        ui.runOnIdle {
            assertEquals(1, permission.settingsOpens)
            assertEquals(0, permission.requests)
            assertEquals(0, permission.contentStarts)
            permission.lifecycleOwner.pause()
            permission.granted = true
            permission.lifecycleOwner.resume()
        }
        ui.onNodeWithText("应用内容").assertIsDisplayed()
        ui.runOnIdle { assertEquals(1, permission.contentStarts) }
    }

    @Test fun permission_revocation_on_return_removes_network_capable_content() {
        val permission = PermissionFixture().apply { granted = true; requested = true }
        render(permission)
        ui.onNodeWithText("应用内容").assertIsDisplayed()
        ui.runOnIdle {
            permission.lifecycleOwner.pause()
            permission.granted = false
            permission.lifecycleOwner.resume()
        }
        ui.onNodeWithText("应用内容").assertDoesNotExist()
        ui.onNodeWithText("前往设置").assertIsDisplayed()
        ui.runOnIdle { assertEquals(0, permission.requests) }
    }

    @Test fun configurations_that_do_not_need_lan_permission_start_without_a_prompt() {
        val permission = PermissionFixture()
        render(permission, required = false)
        ui.onNodeWithText("应用内容").assertIsDisplayed()
        ui.onNodeWithText("允许连接本地服务").assertDoesNotExist()
        ui.runOnIdle {
            assertEquals(0, permission.requests)
            assertEquals(1, permission.contentStarts)
        }
    }

    private fun render(permission: PermissionFixture, required: Boolean = true) {
        ui.setContent {
            val lifecycleOwner = remember { TestOwner() }.also { permission.lifecycleOwner = it }
            CompositionLocalProvider(LocalLifecycleOwner provides lifecycleOwner) {
                BankAgentTheme {
                    LocalNetworkAccessGate(
                        permissionRequired = required,
                        permissionRevision = permission.revision,
                        requestedBefore = permission.requested,
                        readPermissionGranted = { permission.granted },
                        readShouldShowRationale = { permission.rationale },
                        onRequestPermission = { permission.requests++ },
                        onOpenSettings = { permission.settingsOpens++ },
                    ) { NetworkContent(permission) }
                }
            }
        }
    }

    @Composable private fun NetworkContent(permission: PermissionFixture) {
        remember { permission.contentStarts++ }
        Text("应用内容")
    }

    private class PermissionFixture {
        // These reads deliberately aren't Compose state: lifecycle refresh must
        // query the system again even when no permission-result callback ran.
        var granted = false
        var rationale = false
        var requested by mutableStateOf(false)
        var revision by mutableIntStateOf(0)
        var requests = 0
        var settingsOpens = 0
        var contentStarts = 0
        lateinit var lifecycleOwner: TestOwner

        fun completeRequest(granted: Boolean, rationale: Boolean) {
            this.granted = granted
            this.rationale = rationale
            requested = true
            revision++
        }
    }

    private class TestOwner : LifecycleOwner {
        private val registry = LifecycleRegistry(this).apply { currentState = Lifecycle.State.RESUMED }
        override val lifecycle: Lifecycle get() = registry
        fun pause() { registry.currentState = Lifecycle.State.CREATED }
        fun resume() { registry.currentState = Lifecycle.State.RESUMED }
    }
}
