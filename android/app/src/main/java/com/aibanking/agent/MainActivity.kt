package com.aibanking.agent

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
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
import com.aibanking.agent.ui.components.BankSectionCard
import com.aibanking.agent.ui.theme.BankSpacing
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
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

@Composable
private fun RestoringScreen() {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(
                        MaterialTheme.colorScheme.primary.copy(alpha = 0.08f),
                        MaterialTheme.colorScheme.background,
                    )
                )
            ),
        contentAlignment = Alignment.Center,
    ) {
        BankSectionCard(modifier = Modifier.padding(BankSpacing.xxl)) {
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(16.dp),
                modifier = Modifier.padding(horizontal = BankSpacing.xl, vertical = BankSpacing.lg),
            ) {
                Box(
                    modifier = Modifier
                        .size(56.dp)
                        .clip(MaterialTheme.shapes.medium)
                        .background(MaterialTheme.colorScheme.primaryContainer),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Default.Lock,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(32.dp),
                    )
                }
                CircularProgressIndicator(
                    modifier = Modifier.size(32.dp),
                    strokeWidth = 3.dp,
                    color = MaterialTheme.colorScheme.primary,
                )
                Text(
                    "正在恢复安全会话…",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    "Keystore 硬件密钥解密中",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun LoginScreen(
    busy: Boolean,
    otpRequired: Boolean,
    error: String?,
    onDismissError: () -> Unit,
    onLogin: (String, String, String?) -> Unit,
) {
    var userId by remember { mutableStateOf("user_zhang") }
    var password by remember { mutableStateOf("") }
    var passwordVisible by remember { mutableStateOf(false) }
    var otp by remember { mutableStateOf("") }

    val semantics = LocalBankSemanticColors.current
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(
                        MaterialTheme.colorScheme.primary.copy(alpha = 0.12f),
                        MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f),
                        MaterialTheme.colorScheme.background,
                    )
                )
            ),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
            .padding(BankSpacing.xl)
            .widthIn(max = BankSpacing.contentMaxWidth),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            // 品牌与安全标徽
            Box(
                modifier = Modifier
                    .size(64.dp)
                    .clip(MaterialTheme.shapes.large)
                    .background(
                        Brush.linearGradient(
                            listOf(
                                MaterialTheme.colorScheme.primary,
                                MaterialTheme.colorScheme.secondary,
                            )
                        )
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    Icons.Default.Lock,
                    contentDescription = "安全中心",
                    tint = MaterialTheme.colorScheme.onPrimary,
                    modifier = Modifier.size(36.dp),
                )
            }

            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(
                    "安言 AI 银行",
                    style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.height(4.dp))
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .size(8.dp)
                            .clip(MaterialTheme.shapes.extraSmall)
                            .background(semantics.riskGreen)
                    )
                    Text(
                        "受控金融智能体沙箱 · 硬件信任根",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            BankSectionCard {
                Column(
                    modifier = Modifier.padding(BankSpacing.lg),
                    verticalArrangement = Arrangement.spacedBy(BankSpacing.sm),
                ) {
                    OutlinedTextField(
                        value = userId,
                        onValueChange = { userId = it },
                        label = { Text("用户标识") },
                        singleLine = true,
                        leadingIcon = {
                            Icon(Icons.Default.AccountCircle, contentDescription = null)
                        },
                        modifier = Modifier.fillMaxWidth().testTag(UiTags.LOGIN_USER),
                    )

                    OutlinedTextField(
                        value = password,
                        onValueChange = { password = it },
                        label = { Text("登录口令") },
                        singleLine = true,
                        leadingIcon = {
                            Icon(Icons.Default.Lock, contentDescription = null)
                        },
                        trailingIcon = {
                            TextButton(onClick = { passwordVisible = !passwordVisible }) {
                                Text(
                                    if (passwordVisible) "隐藏" else "显示",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.primary,
                                )
                            }
                        },
                        visualTransformation = if (passwordVisible) androidx.compose.ui.text.input.VisualTransformation.None else PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                        modifier = Modifier.fillMaxWidth().testTag(UiTags.LOGIN_PASSWORD),
                    )

                    if (otpRequired) {
                        OutlinedTextField(
                            value = otp,
                            onValueChange = { otp = it.filter(Char::isDigit).take(8) },
                            label = { Text("二次短信验证码") },
                            singleLine = true,
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                            modifier = Modifier.fillMaxWidth().testTag(UiTags.LOGIN_OTP),
                        )
                    }

                    if (error != null) {
                        Surface(
                            color = MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.7f),
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Row(
                                modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(
                                    text = error,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onErrorContainer,
                                    modifier = Modifier.weight(1f),
                                )
                                TextButton(onClick = onDismissError) {
                                    Text("知道了", style = MaterialTheme.typography.labelMedium)
                                }
                            }
                        }
                    }

                    Button(
                        onClick = { onLogin(userId, password, otp.takeIf { it.isNotBlank() }) },
                        enabled = !busy,
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(min = BankSpacing.minTouchTarget)
                            .testTag(UiTags.LOGIN_BUTTON),
                    ) {
                        if (busy) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(20.dp),
                                strokeWidth = 2.5.dp,
                                color = MaterialTheme.colorScheme.onPrimary,
                            )
                            Spacer(Modifier.size(10.dp))
                            Text("安全鉴权中…")
                        } else {
                            Text("立即登录", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.labelLarge)
                        }
                    }
                }
            }

            // 快捷填充演示账户
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    "点击快捷填入测试账户（默认口令 Bank@2026）",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    listOf("user_zhang" to "张三", "user_li" to "李四", "user_wife" to "爱人").forEach { (uid, name) ->
                        AssistChip(
                            onClick = {
                                userId = uid
                                password = "Bank@2026"
                            },
                            label = { Text("$name ($uid)", style = MaterialTheme.typography.labelSmall) },
                            colors = AssistChipDefaults.assistChipColors(
                                containerColor = if (userId == uid) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface
                            ),
                        )
                    }
                }
            }
        }
    }
}
