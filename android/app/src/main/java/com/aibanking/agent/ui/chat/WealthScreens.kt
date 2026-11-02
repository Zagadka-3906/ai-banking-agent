package com.aibanking.agent.ui.chat

import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.anim.PredictiveBackPreview
import com.aibanking.agent.ui.anim.pageSlideSpec
import com.aibanking.agent.ui.components.BankSemanticStatus
import com.aibanking.agent.ui.components.BankStatusChip
import com.aibanking.agent.ui.theme.LocalBankSemanticColors

internal val WealthPositive: Color @Composable get() = LocalBankSemanticColors.current.positive
internal val WealthNegative: Color @Composable get() = LocalBankSemanticColors.current.negative
internal val WealthRiskYellow: Color @Composable get() = LocalBankSemanticColors.current.riskYellow
internal val WealthChartBenchmark: Color @Composable get() = LocalBankSemanticColors.current.chartBenchmark

@Composable
fun WealthUi(state: ChatUiState, viewModel: ChatViewModel, snackbar: SnackbarHostState) {
    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        PredictiveBackPreview(enabled = state.wealthScreen != WealthScreen.LIST,
            onBack = viewModel::backToWealthList,
            beneath = { WealthListScreen(state, viewModel) }) {
            AnimatedContent(targetState = state.wealthScreen,
                transitionSpec = { pageSlideSpec { it.ordinal } }, label = "wealthScreen") { screen ->
                when (screen) {
                    WealthScreen.LIST -> WealthListScreen(state, viewModel)
                    WealthScreen.DETAIL -> WealthDetailScreen(state, viewModel, snackbar)
                }
            }
        }
    }
}

@Composable
private fun WealthListScreen(state: ChatUiState, viewModel: ChatViewModel) {
    WealthMarketplace(state, onClose = viewModel::closeWealth, onProduct = viewModel::showProductDetail,
        onGroup = viewModel::selectWealthMarketGroup, onRefresh = viewModel::refreshWealth,
        onAssess = {
            viewModel.onInputChange(if (state.riskProfile == null) "我想做风险测评" else "我想重新做风险测评")
            viewModel.closeWealth()
        })
}

@Composable
internal fun CategoryChip(text: String) {
    Box(
        Modifier
            .background(MaterialTheme.colorScheme.secondaryContainer, RoundedCornerShape(8.dp))
            .padding(horizontal = 10.dp, vertical = 6.dp),
    ) {
        Text(text, style = MaterialTheme.typography.labelSmall)
    }
}

/** Product risk level, coloured the same on the wealth page and in chat cards. */
@Composable
internal fun RiskBadge(level: String) {
    val status = when (level) {
        "R1", "R2" -> BankSemanticStatus.RiskGreen
        "R3" -> BankSemanticStatus.RiskYellow
        "R4", "R5" -> BankSemanticStatus.RiskRed
        else -> BankSemanticStatus.Neutral
    }
    BankStatusChip(level, status)
}

@Composable
internal fun WealthDetailRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(96.dp),
        )
        Text(value, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
    }
}

internal fun profileLabel(profile: String): String = when (profile) {
    "CONSERVATIVE", "C1" -> "保守型"
    "C2" -> "稳健型"
    "BALANCED", "C3" -> "平衡型"
    "C4" -> "进取型"
    "AGGRESSIVE", "C5" -> "激进型"
    else -> profile
}
