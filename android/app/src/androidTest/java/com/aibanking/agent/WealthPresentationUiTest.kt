package com.aibanking.agent

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.chat.WealthSummary
import com.aibanking.agent.ui.chat.WealthTradingRules
import com.aibanking.agent.ui.theme.BankAgentTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Missing API fields must remain unknown, including in the visible product cards. */
@RunWith(AndroidJUnit4::class)
@BankingRegression
class WealthPresentationUiTest {
    @get:Rule val ui = createAndroidComposeRule<ComponentActivity>()

    @Test fun sparse_product_summary_does_not_invent_ratings_discounts_or_daily_returns() {
        render { WealthSummary(ProductView(name = "资料缺失产品", riskLevel = "R2", recentMonthReturnPct = 12.34)) {} }
        ui.onNodeWithText("期限未提供").assertExists()
        ui.onNodeWithText("评级信息未提供").performScrollTo().assertIsDisplayed()
        ui.onNodeWithText("买入费率未提供").performScrollTo().assertIsDisplayed()
        ui.onNodeWithText("12.34%", substring = true).assertDoesNotExist()
        ui.onNodeWithText("晨星", substring = true).assertDoesNotExist()
        ui.onNodeWithText("0.10%", substring = true).assertDoesNotExist()
    }

    @Test fun missing_rules_do_not_promote_fixed_confirmation_or_fee_promises() {
        render { WealthTradingRules(ProductView()) }
        ui.onNodeWithText("买入确认时间未提供").performScrollTo().assertIsDisplayed()
        ui.onNodeWithText("买入费率未提供").performScrollTo().assertIsDisplayed()
        ui.onNodeWithText("T+1", substring = true).assertDoesNotExist()
        ui.onNodeWithText("T+2", substring = true).assertDoesNotExist()
        ui.onNodeWithText("1折", substring = true).assertDoesNotExist()
    }

    @Test fun provided_summary_data_and_zero_fee_remain_visible() {
        render { WealthSummary(ProductView(name = "完整资料产品", riskLevel = "R1", dailyChangePct = 0.45,
            recentMonthReturnPct = 12.34, discountFeeRate = 0.0, ratingAgency = "演示评级机构",
            promoSubtitle = "限时开放申购")) {} }
        ui.onNodeWithText("+0.45%").assertExists()
        ui.onNodeWithText("演示评级机构").performScrollTo().assertIsDisplayed()
        ui.onNodeWithText("限时开放申购").performScrollTo().assertIsDisplayed()
        ui.onNodeWithText("买入费率 0.00%").performScrollTo().assertIsDisplayed()
        ui.onNodeWithText("12.34%", substring = true).assertDoesNotExist()
    }

    @Test fun product_supplied_rules_remain_complete_on_a_narrow_screen_with_large_text() {
        render(narrow = true) { WealthTradingRules(ProductView(confirmTime = "T+7", discountFeeRate = 0.6, originalFeeRate = 1.5,
            purchaseRule = "按产品开放日申请", redemptionRule = "每月开放赎回")) }
        listOf("提交申请", "核对产品金额", "安全验证", "确认后办理", "查看回执", "核对实际结果")
            .forEach { text ->
                ui.onNodeWithText(text).performScrollTo().assertIsDisplayed()
                assertNoTextOverflow(text)
            }
        ui.onNodeWithText("买入确认T+7").performScrollTo().assertIsDisplayed()
        ui.onNodeWithText("买入费率 0.60% · 原费率 1.50%").performScrollTo().assertIsDisplayed()
        assertNoTextOverflow("买入费率 0.60% · 原费率 1.50%")
        ui.onNodeWithText("每月开放赎回").performScrollTo().assertIsDisplayed()
        val footer = "以上为产品资料，办理状态与到账结果以实际回执为准。"
        ui.onNodeWithText(footer).performScrollTo().assertIsDisplayed()
        assertNoTextOverflow(footer)
        ui.onNodeWithText("1折", substring = true).assertDoesNotExist()
    }

    private fun assertNoTextOverflow(text: String) {
        ui.onNodeWithText(text, useUnmergedTree = true).assertTextFullyVisible(text)
    }

    private fun render(narrow: Boolean = false, content: @Composable () -> Unit) {
        ui.setContent {
            CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density, if (narrow) 1.6f else 1f)) {
                BankAgentTheme {
                    val viewport = if (narrow) Modifier.size(320.dp, 480.dp) else Modifier.fillMaxSize()
                    Column(viewport.verticalScroll(rememberScrollState())) { content() }
                }
            }
        }
    }
}
