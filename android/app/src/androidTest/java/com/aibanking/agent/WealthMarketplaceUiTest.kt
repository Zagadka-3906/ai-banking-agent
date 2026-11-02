package com.aibanking.agent

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.chat.*
import com.aibanking.agent.ui.theme.BankAgentTheme
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/** Category navigation, real filters and a single rich reply, without payments. */
@BankingRegression
class WealthMarketplaceUiTest {
    @get:Rule val ui = createAndroidComposeRule<ComponentActivity>()
    private val products = listOf(
        ProductView(productId = "LX00118", name = "零星宝·活钱管家", marketGroup = "cash", riskLevel = "R1", minAmount = 1.0, holdingDays = 0, annualizedYield = .0205, liquidity = "灵活取用"),
        ProductView(productId = "QN10007", name = "青宁·7天短债", marketGroup = "low_volatility", riskLevel = "R1", minAmount = 100.0, holdingDays = 7, annualizedYield = .0218, liquidity = "7 天最短持有"),
        ProductView(productId = "HQ30060", name = "恒泉稳利·60天持有", marketGroup = "low_volatility", riskLevel = "R2", minAmount = 1000.0, holdingDays = 60, annualizedYield = .0265, liquidity = "60 天最短持有"),
        ProductView(productId = "FH20030", name = "丰禾·30天稳益", marketGroup = "steady_growth", riskLevel = "R2", minAmount = 1000.0, holdingDays = 30, annualizedYield = .0279, liquidity = "30 天最短持有"),
        ProductView(productId = "SG20270", name = "双桂平衡·股债270天", marketGroup = "balanced_growth", riskLevel = "R3", minAmount = 10000.0, holdingDays = 270, strategy = "多资产", annualReturnPct = 7.85, liquidity = "270 天定开"),
        ProductView(productId = "QH30090", name = "千禾·量化平衡90天", marketGroup = "balanced_growth", riskLevel = "R3", minAmount = 5000.0, holdingDays = 90, strategy = "量化策略", annualReturnPct = 3.15, liquidity = "90 天最短持有"),
        ProductView(productId = "YH50365", name = "远航智造·产业精选1年", marketGroup = "growth", riskLevel = "R4", minAmount = 50000.0, holdingDays = 365, annualReturnPct = -4.12, liquidity = "365 天封闭"),
        ProductView(productId = "XH70730", name = "星汉·全球多策略2年", marketGroup = "growth", riskLevel = "R5", minAmount = 100000.0, holdingDays = 730, annualReturnPct = 8.4, liquidity = "730 天封闭"),
    )

    @Test fun all_five_sections_are_reachable_and_more_opens_a_working_term_filter() {
        val chosen = mutableListOf<String>()
        render(onProduct = { chosen += it })
        for (group in WealthMarketGroups) {
            ui.onNodeWithTag(UiTags.WEALTH_LIST).performScrollToNode(hasTestTag("wealth_section_${group.id}"))
            ui.onNodeWithTag("wealth_section_${group.id}").assertExists()
        }
        ui.onNodeWithTag(UiTags.WEALTH_LIST).performScrollToNode(hasTestTag("wealth_more_low_volatility"))
        ui.onNodeWithTag("wealth_more_low_volatility").performClick()
        ui.onNodeWithTag(UiTags.WEALTH_LIST).performScrollToNode(hasTestTag("wealth_filter_low_volatility_1个月内"))
        ui.onNodeWithTag("wealth_filter_low_volatility_1个月内").performClick()
        ui.onNodeWithTag(UiTags.WEALTH_LIST).performScrollToNode(hasTestTag(UiTags.wealthProduct("QN10007")))
        ui.onNodeWithTag(UiTags.wealthProduct("HQ30060")).assertDoesNotExist()
        ui.onNodeWithTag(UiTags.wealthProduct("QN10007")).performClick()
        assertEquals(listOf("QN10007"), chosen)
        screenshot("wealth-filtered-low-volatility")
    }

    @Test fun search_and_strategy_filters_work_with_large_text_in_dark_mode() {
        render(dark = true, fontScale = 1.4f, initialGroup = "balanced_growth")
        ui.onNodeWithTag(UiTags.WEALTH_LIST).performScrollToNode(hasTestTag("wealth_filter_balanced_growth_量化策略"))
        ui.onNodeWithTag("wealth_filter_balanced_growth_量化策略").performScrollTo().performClick()
        ui.onNodeWithTag(UiTags.WEALTH_LIST).performScrollToNode(hasTestTag(UiTags.wealthProduct("QH30090")))
        ui.onNodeWithText("近1年涨跌幅（演示）").assertIsDisplayed()
        screenshot("wealth-dark-large-text")
        ui.onNodeWithTag(UiTags.WEALTH_LIST).performScrollToNode(hasTestTag("wealth_search"))
        ui.onNodeWithTag("wealth_search").performTextInput("不存在的产品")
        ui.onNodeWithTag(UiTags.WEALTH_LIST).performScrollToNode(hasText("这个条件下暂无产品，试试其他期限、策略或关键词。"))
        ui.onNodeWithText("这个条件下暂无产品，试试其他期限、策略或关键词。").assertIsDisplayed()
    }

    @Test fun a_rich_wealth_reply_renders_the_product_once_and_keeps_navigation() {
        val chosen = mutableListOf<String>()
        val data: Map<String, Any> = mapOf("code" to 200, "data" to listOf(
            mapOf("product_id" to "LX00118", "name" to "零星宝·活钱管家", "risk_level" to "R1", "min_amount" to 1,
                "market_group" to "cash", "annualized_yield" to .0205, "liquidity" to "灵活取用")))
        ui.setContent { BankAgentTheme {
            Surface { AgentReplyCard(ChatMessage(role = ChatRole.ASSISTANT,
                text = "零星宝·活钱管家，起购1元。这里是重复的产品正文。",
                result = mapOf("execution_result" to mapOf("type" to "WEALTH_QUERY", "data" to data))), onWealthProduct = { chosen += it }) }
        } }
        ui.onAllNodesWithText("零星宝·活钱管家").assertCountEquals(1)
        ui.onNodeWithText("这里是重复的产品正文。", substring = true).assertDoesNotExist()
        ui.onNodeWithTag(UiTags.wealthProduct("LX00118")).performClick()
        assertEquals(listOf("LX00118"), chosen)
        screenshot("wealth-single-rich-reply")
    }

    private fun render(dark: Boolean = false, fontScale: Float = 1f, initialGroup: String? = null, onProduct: (String) -> Unit = {}) {
        ui.setContent {
            var state by remember { mutableStateOf(ChatUiState(wealthProducts = products, wealthMarketGroupId = initialGroup)) }
            CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density, fontScale), LocalBankMotionEnabled provides false) {
                BankAgentTheme(darkTheme = dark) { Surface(Modifier.size(360.dp, 760.dp), color = MaterialTheme.colorScheme.background) {
                    WealthMarketplace(state, onClose = {}, onProduct = onProduct,
                        onGroup = { state = state.copy(wealthMarketGroupId = it) }, onAssess = {}, onRefresh = {})
                } }
            }
        }
    }

    private fun screenshot(name: String) {
        ui.waitForIdle()
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val folder = File(context.getExternalFilesDir(null), "wealth-review").also { it.mkdirs() }
        File(folder, "$name.png").outputStream().use { ui.onRoot().captureToImage().asAndroidBitmap().compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
    }
}
