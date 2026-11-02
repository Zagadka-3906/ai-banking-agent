package com.aibanking.agent.ui.chat

import androidx.compose.ui.text.TextStyle
import com.aibanking.agent.data.ContactView
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.theme.tabular
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

class UiEnhancementsTest {
    private val sampleToday = LocalDate.of(2026, 10, 26)

    @Test
    fun birthdayValidation_handlesLeapYearAndFutureDates() {
        assertTrue(isValidContactBirthday("2000-02-29", sampleToday))
        assertFalse(isValidContactBirthday("2023-02-29", sampleToday))
        assertTrue(isValidContactBirthday("2026-10-26", sampleToday))
        assertFalse(isValidContactBirthday("2026-10-27", sampleToday))
        assertFalse(isValidContactBirthday("1899-12-31", sampleToday))
        assertFalse(isValidContactBirthday("1995/08/16", sampleToday))
    }

    @Test
    fun dateWheelClamping_clampsLeapYearAndCurrentMonthDays() {
        assertEquals(29, daysInBirthdayMonth(2024, 2, sampleToday))
        assertEquals(28, daysInBirthdayMonth(2025, 2, sampleToday))
        assertEquals(26, daysInBirthdayMonth(2026, 10, sampleToday))
        assertEquals(10, maxBirthdayMonth(2026, sampleToday))
        assertEquals(12, maxBirthdayMonth(2025, sampleToday))

        assertEquals(LocalDate.of(2025, 2, 28), clampBirthdayDate(2025, 2, 31, sampleToday))
        assertEquals(LocalDate.of(2026, 10, 26), clampBirthdayDate(2026, 12, 31, sampleToday))
        assertEquals(LocalDate.of(1900, 1, 1), clampBirthdayDate(1850, 1, 1, sampleToday))
    }

    @Test
    fun wheelPerspective_computesRotationScaleAndAlpha() {
        val center = calculateWheelItemPerspective(0f)
        assertEquals(0f, center.rotationX, 0.01f)
        assertEquals(1f, center.scale, 0.01f)
        assertEquals(1f, center.alpha, 0.01f)

        val oneBelow = calculateWheelItemPerspective(1f)
        assertEquals(-22f, oneBelow.rotationX, 0.01f)
        assertEquals(0.92f, oneBelow.scale, 0.01f)
        assertTrue(oneBelow.alpha in 0.5f..0.65f)

        val oneAbove = calculateWheelItemPerspective(-1f)
        assertEquals(22f, oneAbove.rotationX, 0.01f)
        assertEquals(0.92f, oneAbove.scale, 0.01f)
        assertTrue(oneAbove.alpha in 0.5f..0.65f)

        val farOut = calculateWheelItemPerspective(2.5f)
        assertEquals(0f, farOut.alpha, 0.01f)
    }

    @Test
    fun wheelOverscrollResistance_increasesAsOverscrollGrows() {
        val r0 = calculateWheelOverscrollResistance(0f)
        val r1 = calculateWheelOverscrollResistance(1.5f)
        val r3 = calculateWheelOverscrollResistance(3.0f)

        assertEquals(0.42f, r0, 0.01f)
        assertTrue(r1 < r0)
        assertTrue(r3 < r1)
        assertTrue(r3 > 0f)
    }

    @Test
    fun projectInertialWheelTarget_snapsWithinBoundsAndBouncesOutOfBounds() {
        // Steady release within bounds snaps to nearest integer
        assertEquals(5f, projectInertialWheelTarget(current = 4.8f, velocityItemsPerSec = 0f, maxIndex = 10f), 0.01f)
        assertEquals(7f, projectInertialWheelTarget(current = 5.0f, velocityItemsPerSec = 4.0f, maxIndex = 10f), 0.01f)

        // Overshoot boundaries project to the edge for spring bounce
        assertEquals(0f, projectInertialWheelTarget(current = 1.0f, velocityItemsPerSec = -10.0f, maxIndex = 10f), 0.01f)
        assertEquals(10f, projectInertialWheelTarget(current = 9.0f, velocityItemsPerSec = 10.0f, maxIndex = 10f), 0.01f)

        // Already out of bounds returns boundary
        assertEquals(0f, projectInertialWheelTarget(current = -0.5f, velocityItemsPerSec = -2.0f, maxIndex = 10f), 0.01f)
        assertEquals(10f, projectInertialWheelTarget(current = 10.5f, velocityItemsPerSec = 2.0f, maxIndex = 10f), 0.01f)
    }

    @Test
    fun purchaseStepperAndLiveSummary_computeShortfallAndEstimatedFee() {
        val product = ProductView(
            productId = "LX00118",
            name = "稳盈月月开",
            minAmount = 1000.0,
            discountFeeRate = 0.15,
            confirmTime = "T+1",
        )

        assertNull(wealthLivePurchaseSummary(product, ""))
        val shortfall = wealthLivePurchaseSummary(product, "600")
        assertNotNull(shortfall)
        assertTrue(shortfall!!.contains("距起购金额还差 ¥400.00"))

        val eligibleSummary = wealthLivePurchaseSummary(product, "2000")
        assertNotNull(eligibleSummary)
        assertTrue(eligibleSummary!!.contains("预估买入费用 ¥3.00"))
        assertTrue(eligibleSummary.contains("买入确认T+1"))

        assertEquals("1100", stepPurchaseAmount("1000", 100.0, 1000.0))
        assertEquals("1000", stepPurchaseAmount("1000", -100.0, 1000.0))
        assertEquals("2000", stepPurchaseAmount("1000", 1000.0, 1000.0))
    }

    @Test
    fun purchaseSteps_respect_real_minimums_and_keep_decimal_precision() {
        val product = ProductView(productId = "LX00118", name = "现金管理", minAmount = 1.0)
        assertEquals("1", purchaseMinimumText(product))
        assertEquals("1", stepPurchaseAmount("50", -100.0, 1.0))
        assertEquals("101.01", stepPurchaseAmount("1.01", 100.0, 1.0))
        assertEquals("0.01", stepPurchaseAmount("0.02", -100.0, 0.01))
        assertEquals("1000000000.01", stepPurchaseAmount("999999900.01", 100.0, 1.0))
        assertNull(wealthAmountError(product, "1.01"))
        assertTrue(wealthAmountError(product, "0.50")!!.contains("¥0.50"))
        assertNotNull(wealthAmountError(product, "1.001"))
    }

    @Test
    fun estimatedFee_rounds_decimal_half_cent_explicitly() {
        val product = ProductView(productId = "test", name = "test", minAmount = 1.0, feeRate = 0.5)
        assertTrue(wealthLivePurchaseSummary(product, "1")!!.contains("¥0.01"))
    }

    @Test
    fun composerAutocomplete_matchesTransferPrefixesWithoutDigits() {
        val contacts = listOf(
            ContactView(contactId = "c1", name = "张三", phone = "13800000001"),
            ContactView(contactId = "c2", name = "李四", phone = "13800000002"),
        )

        val fromGive = composerAutocompleteSuggestions("给", contacts)
        assertEquals(2, fromGive.size)
        assertEquals("给张三转", fromGive[0].fillText)

        val fromGivePrefix = composerAutocompleteSuggestions("给李", contacts)
        assertEquals(1, fromGivePrefix.size)
        assertEquals("给李四转", fromGivePrefix[0].fillText)

        assertTrue(composerAutocompleteSuggestions("给张三转 100", contacts).isEmpty())
        assertTrue(composerAutocompleteSuggestions("查一下余额", contacts).isEmpty())
    }

    @Test
    fun tabularTextStyleExtension_setsTnumFeature() {
        val style = TextStyle().tabular()
        assertEquals("tnum", style.fontFeatureSettings)
    }

    @Test
    fun replyTimelineNode_initializesWithExpectedDefaults() {
        val node = ReplyTimelineNode("1", "登记争议", "争议工单已建立", "COMPLETED")
        assertEquals("1", node.step)
        assertEquals("登记争议", node.title)
        assertEquals("争议工单已建立", node.description)
        assertEquals("COMPLETED", node.status)
    }

    @Test
    fun businessStatusLabels_coverCoreFinancialStates() {
        assertEquals("已核验", businessStatusLabel("SUCCEEDED"))
        assertEquals("未完成", businessStatusLabel("FAILED"))
        assertEquals("未通过核验", businessStatusLabel("BLOCKED"))
        assertEquals("待补充", businessStatusLabel("NEEDS_INPUT"))
        assertEquals("待确认", businessStatusLabel("WAITING_APPROVAL"))
        assertEquals("待核对", businessStatusLabel("PENDING_RECONCILIATION"))
        assertEquals("部分完成", businessStatusLabel("PARTIAL_SUCCESS"))
        assertEquals("已取消", businessStatusLabel("CANCELLED"))
    }
}
