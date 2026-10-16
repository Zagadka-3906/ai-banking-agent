package com.aibanking.agent

import com.aibanking.agent.ui.anim.RiskTier
import com.aibanking.agent.ui.anim.glowColor
import com.aibanking.agent.ui.anim.riskTierOf
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
}
