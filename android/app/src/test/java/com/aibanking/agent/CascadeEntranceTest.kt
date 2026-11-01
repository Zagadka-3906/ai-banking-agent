package com.aibanking.agent

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.anim.CascadeEntranceSpec
import com.aibanking.agent.ui.anim.CascadeEntranceState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CascadeEntranceTest {

    @Test
    fun cascade_spec_matches_design_requirements() {
        val spec = CascadeEntranceSpec.ContactCascade
        assertEquals(300, spec.durationMillis)
        assertEquals(40L, spec.staggerDelayMillis)
        assertEquals(FastOutSlowInEasing, spec.easing)
        assertEquals(8, spec.maxAnimatedItems)
        assertTrue(spec.displacement.value in 30f..50f)
        assertEquals(36.dp, spec.displacement)
        assertTrue(spec.windowDurationMs >= (spec.maxAnimatedItems - 1) * spec.staggerDelayMillis + spec.durationMillis)
    }

    @Test
    fun items_do_not_participate_before_begin() {
        val currentTime = 1000L
        val state = CascadeEntranceState(clock = { currentTime })
        for (i in 0..10) {
            assertFalse("Item $i should not participate before begin()", state.shouldParticipate(i))
        }
    }

    @Test
    fun only_first_8_items_participate_within_window() {
        val currentTime = 1000L
        val state = CascadeEntranceState(clock = { currentTime })
        state.begin()

        // Indices 0..7 should participate
        for (i in 0 until 8) {
            assertTrue("Item $i should participate within entrance window", state.shouldParticipate(i, currentTimeMs = currentTime))
        }

        // Performance control: indices >= 8 must NOT participate to avoid scrolling jank
        for (i in 8..20) {
            assertFalse("Item $i should not participate (performance control)", state.shouldParticipate(i, currentTimeMs = currentTime))
        }

        // Negative indices should not participate
        assertFalse(state.shouldParticipate(-1, currentTimeMs = currentTime))
    }

    @Test
    fun items_do_not_participate_after_window_duration_elapses() {
        val currentTime = 1000L
        val state = CascadeEntranceState(clock = { currentTime })
        state.begin()

        val spec = CascadeEntranceSpec.ContactCascade
        // Inside window (e.g. at 500ms)
        assertTrue(state.shouldParticipate(0, spec, currentTimeMs = 1500L))
        assertTrue(state.shouldParticipate(7, spec, currentTimeMs = 1500L))

        // Just at the window boundary (1000 + 650 = 1650L)
        assertTrue(state.shouldParticipate(0, spec, currentTimeMs = 1650L))

        // After window has closed (e.g. user scrolled past entrance window at 1700L)
        assertFalse("Item should not replay after entrance window closed", state.shouldParticipate(0, spec, currentTimeMs = 1700L))
        assertFalse("Item should not replay after entrance window closed", state.shouldParticipate(5, spec, currentTimeMs = 1700L))
    }

    @Test
    fun refresh_increments_generation_and_resets_window() {
        var currentTime = 1000L
        val state = CascadeEntranceState(clock = { currentTime })
        assertEquals(0, state.generation)

        state.begin()
        assertEquals(1, state.generation)
        assertEquals(1000L, state.windowOpenedAtMs)

        // Advance time beyond window
        currentTime = 3000L
        assertFalse(state.shouldParticipate(0, currentTimeMs = currentTime))

        // User refreshes -> begin() called again
        state.begin()
        assertEquals(2, state.generation)
        assertEquals(3000L, state.windowOpenedAtMs)
        assertTrue("Front items participate again after refresh", state.shouldParticipate(0, currentTimeMs = currentTime))
        assertTrue("Front items participate again after refresh", state.shouldParticipate(7, currentTimeMs = currentTime))
        assertFalse("Performance control still holds after refresh", state.shouldParticipate(8, currentTimeMs = currentTime))
    }

    @Test
    fun wave_stagger_progression_delay() {
        val spec = CascadeEntranceSpec.ContactCascade
        for (index in 0 until spec.maxAnimatedItems) {
            val expectedDelay = index * spec.staggerDelayMillis
            assertEquals(index * 40L, expectedDelay)
        }
        val lastItemStartTime = 7 * spec.staggerDelayMillis
        assertEquals(280L, lastItemStartTime)
        val lastItemEndTime = lastItemStartTime + spec.durationMillis
        assertEquals(580L, lastItemEndTime)
        assertTrue("Total wave finishes before window expires", lastItemEndTime <= spec.windowDurationMs)
    }
}
