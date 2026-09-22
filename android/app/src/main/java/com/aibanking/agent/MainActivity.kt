package com.aibanking.agent

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.viewmodel.compose.viewModel
import com.aibanking.agent.ui.anim.pageSlideSpec
import com.aibanking.agent.ui.auth.LoginScreen
import com.aibanking.agent.ui.auth.RestoringScreen
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
        enableEdgeToEdge()
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
                                // 登录失败（口令错误 / 核心不可达）和“会话已失效”都由这里
                                // 显示：之前 state.error 只有聊天页的 Snackbar 会读，登录页
                                // 完全看不到，输错口令时界面毫无反馈。
                                error = state.error,
                                onDismissError = viewModel::dismissMessages,
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
