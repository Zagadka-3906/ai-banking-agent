package com.aibanking.agent

import com.aibanking.agent.data.ApprovalView
import com.aibanking.agent.ui.anim.RiskSignal
import com.aibanking.agent.ui.anim.RiskTier
import com.aibanking.agent.ui.anim.glowColor
import com.aibanking.agent.ui.anim.riskTierOf
import com.aibanking.agent.ui.chat.activeApprovalRiskSignal
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RiskGlowTest {
    @Test
    fun server_risk_levels_map_to_their_tier_case_insensitively() {
        assertEquals(RiskTier.GREEN, riskTierOf("GREEN"))
        assertEquals(RiskTier.YELLOW, riskTierOf("yellow"))
        assertEquals(RiskTier.RED, riskTierOf("Red"))
    }

    @Test
    fun unknown_or_missing_levels_show_no_ring() {
        assertNull(riskTierOf(null))
        assertNull(riskTierOf(""))
        assertNull(riskTierOf("ORANGE"))
    }

    @Test
    fun each_tier_has_its_own_colour() {
        assertEquals(3, RiskTier.entries.map { it.glowColor() }.toSet().size)
    }

    @Test
    fun activeApprovalRiskSignal_sustainsGlowUntilConfirmationThenDismissesImmediately() {
        val yellowApproval = ApprovalView(approvalId = "yellow-123", mfaRequired = false)
        val redApproval = ApprovalView(approvalId = "red-456", mfaRequired = true)

        // 未做决定时：黄区呼吸黄光，红区呼吸红光
        assertEquals(
            RiskSignal("approval:yellow-123", RiskTier.YELLOW, sustained = true),
            activeApprovalRiskSignal(yellowApproval, confirmed = false),
        )
        assertEquals(
            RiskSignal("approval:red-456", RiskTier.RED, sustained = true),
            activeApprovalRiskSignal(redApproval, confirmed = false),
        )

        // 用户点击最终确认按钮（或拒绝操作）后：光效立刻关闭（null），无需等待 Agent 回复完成
        assertNull(activeApprovalRiskSignal(yellowApproval, confirmed = true))
        assertNull(activeApprovalRiskSignal(redApproval, confirmed = true))

        // 无待审批操作时：光效关闭
        assertNull(activeApprovalRiskSignal(null, confirmed = false))
        assertNull(activeApprovalRiskSignal(null, confirmed = true))
    }
}
