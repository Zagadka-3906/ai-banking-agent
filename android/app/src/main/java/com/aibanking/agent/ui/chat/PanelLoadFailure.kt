package com.aibanking.agent.ui.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.UiTags

/**
 * “加载失败”占位，代替面板的空状态。
 *
 * 空列表有两种含义：核心说“你没有”，或者根本没问到核心。后者渲染成“还没有
 * 银行卡 / 还没有联系人 / 暂无持仓”，就是把一次网络故障说成了事实，所以这里
 * 明确报错并给一个重试入口。
 */
@Composable
internal fun PanelLoadFailure(message: String, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxWidth().padding(vertical = 8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(
            "加载失败：$message",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.error,
        )
        TextButton(onClick = onRetry, modifier = Modifier.testTag(UiTags.PANEL_RETRY)) { Text("重试") }
    }
}
