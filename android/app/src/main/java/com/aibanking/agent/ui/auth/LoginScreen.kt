package com.aibanking.agent.ui.auth

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.components.*
import com.aibanking.agent.ui.theme.BankSpacing

@Composable
fun RestoringScreen() {
    Box(Modifier.fillMaxSize().safeDrawingPadding(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(20.dp)) {
            BrandMark()
            Text("安言 AI 银行", style = MaterialTheme.typography.headlineSmall)
            CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 2.dp)
            Text("正在恢复安全会话…", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun BrandMark() {
    Box(
        Modifier.size(56.dp).clip(MaterialTheme.shapes.large).background(MaterialTheme.colorScheme.primary),
        contentAlignment = Alignment.Center,
    ) {
        BankIcon(BankIconKind.Bank, Modifier.size(30.dp), tint = MaterialTheme.colorScheme.onPrimary)
    }
}

@Composable
fun LoginScreen(
    busy: Boolean,
    otpRequired: Boolean,
    error: String?,
    onDismissError: () -> Unit,
    onLogin: (String, String, String?) -> Unit,
) {
    var userId by rememberSaveable { mutableStateOf("user_zhang") }
    var password by remember { mutableStateOf("") }
    var passwordVisible by remember { mutableStateOf(false) }
    var otp by remember { mutableStateOf("") }
    val focus = LocalFocusManager.current
    val canLogin = !busy && userId.isNotBlank() && password.isNotBlank() && (!otpRequired || otp.isNotBlank())
    val submit = {
        if (canLogin) {
            focus.clearFocus()
            onLogin(userId.trim(), password, otp.takeIf { it.isNotBlank() })
        }
    }

    Box(Modifier.fillMaxSize().safeDrawingPadding().imePadding(), contentAlignment = Alignment.TopCenter) {
        Column(
            Modifier.widthIn(max = BankSpacing.contentMaxWidth).fillMaxWidth()
                .verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 28.dp),
            verticalArrangement = Arrangement.spacedBy(28.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                BrandMark()
                Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text("安言 AI 银行", style = MaterialTheme.typography.titleLarge)
                    Text("ANYAN · 您的智能金融伙伴", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("让每一笔，\n都心中有数。", style = MaterialTheme.typography.displaySmall, color = MaterialTheme.colorScheme.primary)
                Text("从一句话开始，轻松管理日常财务。", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            BankSectionCard {
                Column(Modifier.animateContentSize(), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("欢迎回来", style = MaterialTheme.typography.titleLarge)
                        BankStatusChip("安全登录", BankSemanticStatus.RiskGreen)
                    }
                    OutlinedTextField(
                        value = userId, onValueChange = { userId = it }, enabled = !busy,
                        label = { Text("用户标识") }, singleLine = true, shape = MaterialTheme.shapes.medium,
                        leadingIcon = { BankIcon(BankIconKind.People) },
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                        modifier = Modifier.fillMaxWidth().testTag(UiTags.LOGIN_USER),
                    )
                    OutlinedTextField(
                        value = password, onValueChange = { password = it }, enabled = !busy,
                        label = { Text("登录口令") }, singleLine = true, shape = MaterialTheme.shapes.medium,
                        leadingIcon = { BankIcon(BankIconKind.Shield) },
                        trailingIcon = {
                            TextButton(onClick = { passwordVisible = !passwordVisible }) {
                                Text(if (passwordVisible) "隐藏" else "显示", style = MaterialTheme.typography.labelMedium)
                            }
                        },
                        visualTransformation = if (passwordVisible) VisualTransformation.None else PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = if (otpRequired) ImeAction.Next else ImeAction.Done),
                        keyboardActions = KeyboardActions(onDone = { submit() }),
                        modifier = Modifier.fillMaxWidth().testTag(UiTags.LOGIN_PASSWORD),
                    )
                    AnimatedVisibility(otpRequired) {
                        OutlinedTextField(
                            value = otp, onValueChange = { otp = it.filter(Char::isDigit).take(8) }, enabled = !busy,
                            label = { Text("二次短信验证码") }, singleLine = true, shape = MaterialTheme.shapes.medium,
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword, imeAction = ImeAction.Done),
                            keyboardActions = KeyboardActions(onDone = { submit() }),
                            modifier = Modifier.fillMaxWidth().testTag(UiTags.LOGIN_OTP),
                        )
                    }
                    AnimatedVisibility(error != null) {
                        Surface(color = MaterialTheme.colorScheme.errorContainer, shape = MaterialTheme.shapes.medium) {
                            Column(Modifier.fillMaxWidth().padding(12.dp)) {
                                Text(error.orEmpty(), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onErrorContainer)
                                TextButton(onClick = onDismissError, modifier = Modifier.align(Alignment.End)) { Text("知道了") }
                            }
                        }
                    }
                    BankPrimaryButton(
                        text = if (busy) "安全鉴权中…" else "立即登录", onClick = submit,
                        enabled = canLogin, loading = busy,
                        modifier = Modifier.fillMaxWidth().testTag(UiTags.LOGIN_BUTTON),
                    )
                }
            }
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("体验演示账户", style = MaterialTheme.typography.titleSmall)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("user_zhang" to "张三", "user_li" to "李四", "user_wife" to "爱人").forEach { (uid, name) ->
                        FilterChip(
                            selected = userId == uid, onClick = { userId = uid; password = "Bank@2026" }, enabled = !busy,
                            label = { Text(name, fontWeight = FontWeight.Medium) },
                            leadingIcon = { BankIcon(BankIconKind.People, Modifier.size(18.dp)) },
                            modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                        )
                    }
                }
                Text("点击填入测试账户 · 默认口令 Bank@2026", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                BankIcon(BankIconKind.Shield, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.primary)
                Text("安全验证 · 每笔资金操作由您确认", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
