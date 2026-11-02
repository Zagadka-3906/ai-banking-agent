package com.aibanking.agent.ui.chat

import android.os.SystemClock
import androidx.compose.animation.core.*
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.ui.UiTags
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalView
import com.aibanking.agent.ui.anim.BankHaptics
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.components.*
import com.aibanking.agent.ui.theme.BankSpacing
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import kotlinx.coroutines.delay

@Composable
internal fun ApprovalCard(
    approval: ApprovalView,
    modifier: Modifier = Modifier,
    deadlineMillis: Long,
    otp: String,
    busy: Boolean,
    canPromptBiometric: Boolean,
    onOtpChange: (String) -> Unit,
    onRequestOtp: () -> Unit,
    onReject: () -> Unit,
    onApprove: () -> Unit,
    onBiometricApprove: () -> Unit,
    error: String? = null,
) {
    val view = LocalView.current
    val isRed = approval.mfaRequired
    val semantic = LocalBankSemanticColors.current
    val riskColor = if (isRed) semantic.riskRed else semantic.riskYellow
    val focusManager = LocalFocusManager.current
    val keyboardController = LocalSoftwareKeyboardController.current
    val otpError = if (otp.isNotEmpty() && !otp.matches(Regex("[0-9]{6}"))) "请输入 6 位数字验证码" else null
    val motion = LocalBankMotionEnabled.current
    fun secondsLeft(): Int = ((deadlineMillis - SystemClock.elapsedRealtime()).coerceAtLeast(0) + 999L).div(1_000L).toInt()
    var remainingSeconds by remember(approval.approvalId, deadlineMillis) { mutableIntStateOf(secondsLeft()) }
    LaunchedEffect(approval.approvalId, deadlineMillis) {
        do {
            val prev = remainingSeconds
            remainingSeconds = secondsLeft()
            if (remainingSeconds != prev && remainingSeconds in 1..5 && motion) {
                BankHaptics.tick(view)
            }
            if (remainingSeconds > 0) delay(250)
        } while (remainingSeconds > 0)
    }
    val isUrgent = remainingSeconds in 1..15
    val isExpired = remainingSeconds == 0
    val heartbeatScale = if (isUrgent && motion) {
        val transition = rememberInfiniteTransition(label = "urgentHeartbeat")
        transition.animateFloat(
            initialValue = 1f,
            targetValue = 1.04f,
            animationSpec = infiniteRepeatable(
                animation = tween(durationMillis = 650, easing = FastOutSlowInEasing),
                repeatMode = RepeatMode.Reverse,
            ),
            label = "heartbeatScale",
        ).value
    } else 1f
    val cardAlpha by animateFloatAsState(
        targetValue = if (isExpired) 0.72f else 1f,
        animationSpec = if (motion) tween(360) else snap(),
        label = "approvalCardAlpha",
    )
    BankSectionCard(modifier = modifier.graphicsLayer { alpha = cardAlpha }.testTag("approval_card"), outlined = true,
        containerColor = MaterialTheme.colorScheme.surfaceContainerLow) {
        Column(verticalArrangement = Arrangement.spacedBy(BankSpacing.sm)) {
            BankStatusChip(if (isRed) "红 · 强验证" else "黄 · 需确认", if (isRed) BankSemanticStatus.RiskRed else BankSemanticStatus.RiskYellow)
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    if (isRed) Icons.Default.Warning else Icons.Default.Info,
                    contentDescription = null,
                    tint = riskColor,
                    modifier = Modifier.size(22.dp),
                )
                Text(
                    text = if (isRed) "验证身份后执行" else "请核对本次操作",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurface,
                )
            }
            Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(16.dp)) {
                Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("本次办理内容", style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    // Keep the server's full summary; never infer or reformat amounts from prose.
                    Text(approval.summary, style = MaterialTheme.typography.bodyLarge.copy(fontFeatureSettings = "tnum"),
                        fontWeight = FontWeight.Medium, modifier = Modifier.testTag("approval_summary"))
                }
            }
            Surface(
                color = MaterialTheme.colorScheme.surface.copy(alpha = 0.7f),
                shape = CircleShape,
                modifier = Modifier.graphicsLayer { scaleX = heartbeatScale; scaleY = heartbeatScale },
            ) {
                Text(
                    text = if (remainingSeconds > 0) "请在 $remainingSeconds 秒内确认" else "已超时，请重新发起操作",
                    style = MaterialTheme.typography.labelSmall,
                    color = if (remainingSeconds <= 15) semantic.riskRed else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
                )
            }
            // A timer is feedback, never a moving or pulsing financial fact.
            val total = approval.expiresIn.coerceAtLeast(1).toFloat()
            val fraction by animateFloatAsState((remainingSeconds / total).coerceIn(0f, 1f),
                if (LocalBankMotionEnabled.current) tween(450, easing = LinearEasing) else snap(), label = "approvalTime")
            LinearProgressIndicator(
                progress = { fraction },
                modifier = Modifier.fillMaxWidth().height(4.dp).clip(CircleShape),
                color = if (remainingSeconds <= 15) semantic.riskRed else riskColor,
                trackColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.6f),
                drawStopIndicator = {},
            )
            if (isRed) {
                OutlinedTextField(
                    colors = bankTextFieldColors(),
                    value = otp,
                    isError = otpError != null || error?.contains("验证码") == true,
                    supportingText = if (otpError != null) { { BankFormError(otpError) } } else null,
                    onValueChange = onOtpChange,
                    label = { Text("短信验证码（6 位）") },
                    singleLine = true,
                    // A one-time code is shown like an SMS code so the user can check it.
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.APPROVAL_OTP),
                )
                TextButton(onClick = onRequestOtp, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.onSurface), enabled = !busy && remainingSeconds > 0) { Text("获取本机一次性演示验证码") }
                if (!canPromptBiometric) {
                    Text(
                        "本设备未录入生物识别凭据，无法完成红色风险强验证。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
            val isDark = isSystemInDarkTheme()
            val approveButtonColors = if (isRed) {
                ButtonDefaults.buttonColors(
                    containerColor = if (isDark) Color(0xFFF28B82) else Color(0xFFB3261E),
                    contentColor = if (isDark) Color(0xFF380704) else Color.White,
                    disabledContainerColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.12f),
                    disabledContentColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
                )
            } else {
                ButtonDefaults.buttonColors(
                    containerColor = if (isDark) Color(0xFFFDD663) else Color(0xFFF9AB00),
                    contentColor = Color(0xFF1F1F1F),
                    disabledContainerColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.12f),
                    disabledContentColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
                )
            }
            BankFormError(error)
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (isRed) {
                    BankPrimaryButton(
                        text = "验证并确认办理",
                        onClick = {
                            remainingSeconds = secondsLeft()
                            if (remainingSeconds > 0) {
                                keyboardController?.hide()
                                focusManager.clearFocus()
                                onBiometricApprove()
                            }
                        },
                        enabled = !busy && remainingSeconds > 0 && canPromptBiometric && otp.matches(Regex("[0-9]{6}")),
                        loading = busy,
                        colors = approveButtonColors,
                        modifier = Modifier.fillMaxWidth().testTag(UiTags.APPROVAL_APPROVE),
                    )
                } else {
                    BankHoldToConfirmButton(
                        text = "长按确认本次操作",
                        onConfirmed = {
                            remainingSeconds = secondsLeft()
                            if (remainingSeconds > 0) {
                                keyboardController?.hide()
                                focusManager.clearFocus()
                                onApprove()
                            }
                        },
                        enabled = !busy && remainingSeconds > 0,
                        loading = busy,
                        modifier = Modifier.fillMaxWidth().testTag(UiTags.APPROVAL_APPROVE),
                    )
                }
                BankSecondaryButton("取消操作", onReject, enabled = !busy,
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.APPROVAL_REJECT))
            }
            Text(
                "请核对办理对象、金额和具体内容。确认后，才会继续本次操作。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
