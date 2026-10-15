package com.aibanking.agent.ui.auth

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.content.edit
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.aibanking.agent.BuildConfig
import com.aibanking.agent.data.requiresLocalNetworkPermission
import com.aibanking.agent.ui.components.BankPrimaryButton
import com.aibanking.agent.ui.components.BankSectionCard
import com.aibanking.agent.ui.theme.BankSpacing

internal const val LOCAL_NETWORK_PERMISSION = "android.permission.ACCESS_LOCAL_NETWORK"

/** Keep session restoration and all network-capable content behind permission. */
@Composable
fun LocalNetworkAccessGate(activity: FragmentActivity, content: @Composable () -> Unit) {
    val required = remember {
        requiresLocalNetworkPermission(
            Build.VERSION.SDK_INT, BuildConfig.CORE_BASE_URL, BuildConfig.GATEWAY_BASE_URL,
            explicitlyRequired = BuildConfig.FORCE_LOCAL_NETWORK_PERMISSION,
        )
    }
    if (!required) {
        content()
        return
    }
    val preferences = remember(activity) { activity.getSharedPreferences("local_network_permission", Context.MODE_PRIVATE) }
    var requestedBefore by remember { mutableStateOf(preferences.getBoolean("requested", false)) }
    var permissionRevision by remember { mutableIntStateOf(0) }
    var requestInFlight by remember { mutableStateOf(false) }
    val request = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        preferences.edit { putBoolean("requested", true) }
        requestedBefore = true
        requestInFlight = false
        permissionRevision++
    }
    LocalNetworkAccessGate(
        permissionRequired = true,
        permissionRevision = permissionRevision,
        requestedBefore = requestedBefore,
        requestInFlight = requestInFlight,
        readPermissionGranted = { ContextCompat.checkSelfPermission(activity, LOCAL_NETWORK_PERMISSION) == PackageManager.PERMISSION_GRANTED },
        readShouldShowRationale = { ActivityCompat.shouldShowRequestPermissionRationale(activity, LOCAL_NETWORK_PERMISSION) },
        onRequestPermission = {
            if (!requestInFlight) {
                requestInFlight = true
                request.launch(LOCAL_NETWORK_PERMISSION)
            }
        },
        onOpenSettings = {
            activity.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", activity.packageName, null)))
        },
        content = content,
    )
}

/** Permission effects are injected so denial, retries and settings return are testable. */
@Composable
internal fun LocalNetworkAccessGate(
    permissionRequired: Boolean,
    permissionRevision: Int,
    requestedBefore: Boolean,
    requestInFlight: Boolean = false,
    readPermissionGranted: () -> Boolean,
    readShouldShowRationale: () -> Boolean,
    onRequestPermission: () -> Unit,
    onOpenSettings: () -> Unit,
    content: @Composable () -> Unit,
) {
    var granted by remember(permissionRequired) { mutableStateOf(!permissionRequired || readPermissionGranted()) }
    var permanentlyDenied by remember { mutableStateOf(requestedBefore && !readShouldShowRationale()) }
    val refresh by rememberUpdatedState {
        granted = !permissionRequired || readPermissionGranted()
        permanentlyDenied = requestedBefore && !readShouldShowRationale()
    }
    LaunchedEffect(permissionRequired, permissionRevision, requestedBefore) { refresh() }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_RESUME) refresh() }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }
    if (granted) {
        content()
    } else {
        Box(Modifier.fillMaxSize().safeDrawingPadding(), contentAlignment = Alignment.Center) {
            Column(
                Modifier.widthIn(max = BankSpacing.contentMaxWidth).fillMaxWidth()
                    .verticalScroll(rememberScrollState()).padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
            ) {
                BankSectionCard {
                    Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        Text("连接本地演示服务", style = MaterialTheme.typography.headlineSmall)
                        Text(
                            if (permanentlyDenied) "请在系统设置中允许访问附近设备，返回后即可继续。"
                            else "安言需要连接您配置的本地服务。请允许访问附近设备，以便登录并使用演示账户。",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        BankPrimaryButton(
                            text = when {
                                requestInFlight -> "等待授权…"
                                permanentlyDenied -> "前往设置"
                                requestedBefore -> "重新授权"
                                else -> "允许连接本地服务"
                            },
                            onClick = if (permanentlyDenied) onOpenSettings else onRequestPermission,
                            enabled = !requestInFlight,
                            modifier = Modifier.fillMaxWidth().testTag("local_network_permission_action"),
                        )
                    }
                }
            }
        }
    }
}
