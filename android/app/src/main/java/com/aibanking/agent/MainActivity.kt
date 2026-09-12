package com.aibanking.agent

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.viewmodel.compose.viewModel
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.pageSlideSpec
import com.aibanking.agent.ui.chat.ChatScreen
import com.aibanking.agent.ui.chat.ChatViewModel
import com.aibanking.agent.ui.theme.BankAgentTheme

/**
 * Single-activity host.
 *
 * It extends [FragmentActivity] rather than a plain ComponentActivity because
 * the red authorization tier needs a `BiometricPrompt`, which is fragment
 * based.
 */
class MainActivity : FragmentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestNotificationPermissionIfNeeded()
        setContent {
            BankAgentTheme {
                Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    val viewModel: ChatViewModel = viewModel()
                    val state by viewModel.state.collectAsState()
                    val page = when {
                        // Unsealing the stored session is Keystore work and runs off
                        // the main thread, so there is a short window before the app
                        // knows whether it is logged in.  Showing the login form
                        // during it would flash a password prompt on every launch.
                        state.restoring -> RootPage.RESTORING
                        state.authenticated -> RootPage.CHAT
                        else -> RootPage.LOGIN
                    }
                    // 根级 Screen 切换（恢复态/登录/聊天）共用同一套推入/弹出转场。
                    AnimatedContent(
                        targetState = page,
                        transitionSpec = { pageSlideSpec(RootPage::ordinal) },
                        label = "rootPage",
                    ) { current ->
                        when (current) {
                            RootPage.RESTORING -> RestoringScreen()
                            RootPage.CHAT -> ChatScreen(viewModel = viewModel, activity = this@MainActivity)
                            RootPage.LOGIN -> LoginScreen(
                                busy = state.busy,
                                otpRequired = state.otpRequiredAtLogin,
                                onLogin = viewModel::login,
                            )
                        }
                    }
                }
            }
        }
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                NOTIFICATION_PERMISSION_REQUEST,
            )
        }
    }
}

/**
 * Ask for the notification permission once, on the Android versions that need it.
 *
 * A push notification that never arrives because the permission was never
 * requested looks exactly like push being broken, so the request belongs next to
 * the code that depends on it.
 *
 * The request code is a small literal on purpose.  This activity extends
 * `FragmentActivity` (BiometricPrompt needs it), and `FragmentActivity` rejects
 * request codes above 0xFFFF - which is what the activity-result registry can
 * hand out, and doing this through `rememberLauncherForActivityResult` crashed
 * the app on launch with "Can only use lower 16 bits for requestCode".
 */
private const val NOTIFICATION_PERMISSION_REQUEST = 1001

/** 根级三个 Screen；顺序即层级深度，登录成功为前进、登出为返回。 */
private enum class RootPage { RESTORING, LOGIN, CHAT }

@Composable
private fun RestoringScreen() {
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            CircularProgressIndicator()
            Text("正在恢复登录状态…", style = MaterialTheme.typography.bodyMedium)
        }
    }
}

@Composable
private fun LoginScreen(
    busy: Boolean,
    otpRequired: Boolean,
    onLogin: (String, String, String?) -> Unit,
) {
    var userId by remember { mutableStateOf("user_zhang") }
    var password by remember { mutableStateOf("") }
    var otp by remember { mutableStateOf("") }
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
    ) {
        Text("AI 银行助手", style = MaterialTheme.typography.headlineSmall)
        Text(
            "登录后所有操作都会绑定到您的会话身份；高风险操作还需要短信验证与设备签名。",
            style = MaterialTheme.typography.bodyMedium,
        )
        OutlinedTextField(
            value = userId,
            onValueChange = { userId = it },
            label = { Text("用户标识") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().testTag(UiTags.LOGIN_USER),
        )
        OutlinedTextField(
            value = password,
            onValueChange = { password = it },
            label = { Text("登录口令") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth().testTag(UiTags.LOGIN_PASSWORD),
        )
        if (otpRequired) {
            // Only shown once the server has actually demanded a second factor.
            OutlinedTextField(
                value = otp,
                onValueChange = { otp = it.filter(Char::isDigit).take(8) },
                label = { Text("短信验证码") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                modifier = Modifier.fillMaxWidth().testTag(UiTags.LOGIN_OTP),
            )
        }
        Button(
            onClick = { onLogin(userId, password, otp.takeIf { it.isNotBlank() }) },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth().testTag(UiTags.LOGIN_BUTTON),
        ) {
            Text(if (busy) "登录中…" else "登录")
        }
        Text(
            "演示账户：user_zhang / user_li / user_wife，口令 Bank@2026",
            style = MaterialTheme.typography.labelSmall,
        )
    }
}
