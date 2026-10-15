package com.aibanking.agent

import com.aibanking.agent.data.ManagerView
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.chat.wealthConfirmationText
import com.aibanking.agent.ui.chat.wealthInstitutionFacts
import com.aibanking.agent.ui.chat.wealthManagerFacts
import com.aibanking.agent.ui.chat.wealthPurchaseFeeText
import org.junit.Assert.*
import org.junit.Test

class WealthPresentationTest {
    @Test fun absent_purchase_fee_does_not_borrow_an_unrelated_fee_or_assume_a_discount() {
        assertEquals("买入费率未提供", wealthPurchaseFeeText(ProductView(feeRate = 0.8)))
        assertEquals("买入费率未提供 · 原费率 1.50%", wealthPurchaseFeeText(ProductView(originalFeeRate = 1.5)))
    }

    @Test fun explicit_zero_and_non_tenth_discount_rates_are_preserved() {
        assertEquals("买入费率 0.00%", wealthPurchaseFeeText(ProductView(discountFeeRate = 0.0)))
        val text = wealthPurchaseFeeText(ProductView(discountFeeRate = 0.6, originalFeeRate = 1.5))
        assertEquals("买入费率 0.60% · 原费率 1.50%", text)
        assertFalse(text.contains("1折"))
    }

    @Test fun invalid_fee_values_are_treated_as_unavailable() {
        for (value in listOf(-0.1, Double.NaN, Double.POSITIVE_INFINITY)) {
            assertEquals("买入费率未提供", wealthPurchaseFeeText(ProductView(discountFeeRate = value, originalFeeRate = value)))
        }
    }

    @Test fun confirmation_time_is_displayed_only_when_the_product_provides_it() {
        for (value in listOf(null, "", " ")) {
            assertEquals("买入确认时间未提供", wealthConfirmationText(ProductView(confirmTime = value)))
        }
        assertEquals("买入确认T+7", wealthConfirmationText(ProductView(confirmTime = "T+7")))
    }

    @Test fun missing_manager_and_institution_details_do_not_inherit_another_products_facts() {
        assertEquals("从业年限与在管规模未提供", wealthManagerFacts(ProductView()))
        assertEquals("管理人：未提供", wealthInstitutionFacts(ProductView()))
        val product = ProductView(manager = ManagerView(experienceYears = 0.0), managerCompany = "演示管理人")
        assertEquals("从业0.0年", wealthManagerFacts(product))
        assertEquals("管理人：演示管理人", wealthInstitutionFacts(product))
    }

    @Test fun available_manager_and_institution_details_are_kept() {
        val product = ProductView(manager = ManagerView(experienceYears = 8.0, managedScaleBillion = 12.5),
            managerCompany = "示例公司", institutionFoundedYears = 9, institutionAumBillion = 25.0,
            institutionRating = "已提供评级")
        assertEquals("从业8.0年 · 在管规模12.5亿元", wealthManagerFacts(product))
        assertEquals("管理人：示例公司 · 成立9年 · 管理规模25.0亿元 · 已提供评级", wealthInstitutionFacts(product))
    }
}
