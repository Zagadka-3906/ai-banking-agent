package com.aibanking.agent

import com.aibanking.agent.ui.anim.shapingOrbFrame
import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
import org.junit.Test

class ThinkingOrbsTest {
    @Test fun shapingPresetMatchesAllFourUpstreamGoldenFrames() {
        // Golden vectors shipped by Jakubantalik/thinking-orbs (MIT).
        val cases = javaClass.getResourceAsStream("/thinking-orbs-shaping20-golden.json")!!.reader().use {
            JsonParser.parseReader(it).asJsonArray
        }
        for (case in cases) {
            val sample = case.asJsonObject
            val actual = shapingOrbFrame(sample["t"].asDouble)
            val expected = sample["dots"].asJsonArray
            assertEquals(sample["dotCount"].asInt, actual.size)
            actual.forEachIndexed { index, dot ->
                listOf(dot.x, dot.y, dot.depth, dot.radius, dot.ink, dot.alpha).forEachIndexed { component, value ->
                    assertEquals("${sample["key"]} dot $index component $component", expected[index * 6 + component].asDouble, value, .0001)
                }
            }
        }
    }
}
