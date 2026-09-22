package com.aibanking.agent.ui.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.components.BankSectionCard
import com.aibanking.agent.ui.components.BankIcon
import com.aibanking.agent.ui.components.BankIconKind
import com.aibanking.agent.ui.components.BankSecondaryButton
import com.aibanking.agent.ui.theme.BankSpacing

/**
 * “加载失败”占位，代替面板的空状态。
 *
 * 空列表有两种含义：核心说“你没有”，或者根本没问到核心。后者渲染成“还没有
 * 银行卡 / 还没有联系人 / 暂无持仓”，就是把一次网络故障说成了事实，所以这里
 * 明确报错并给一个重试入口。
 */
@Composable
internal fun PanelLoadFailure(message: String, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    BankSectionCard(
        modifier = modifier.fillMaxWidth().padding(vertical = BankSpacing.xs).semantics { liveRegion = LiveRegionMode.Polite },
        outlined = true,
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(vertical = 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Surface(shape = CircleShape, color = MaterialTheme.colorScheme.errorContainer) {
                BankIcon(BankIconKind.Refresh, Modifier.padding(16.dp).size(24.dp), tint = MaterialTheme.colorScheme.onErrorContainer)
            }
            Text("暂时无法加载", style = MaterialTheme.typography.titleMedium)
            Text(
                "加载失败：$message",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
            BankSecondaryButton("重试", onRetry, Modifier.testTag(UiTags.PANEL_RETRY))
        }
    }
}
