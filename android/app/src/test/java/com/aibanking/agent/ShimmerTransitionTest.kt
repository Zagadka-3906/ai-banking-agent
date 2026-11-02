package com.aibanking.agent

import androidx.compose.animation.AnimatedContentTransitionScope
import androidx.compose.animation.ContentTransform
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.anim.SHIMMER_FADE_DURATION_MILLIS
import com.aibanking.agent.ui.anim.SHIMMER_RISE_OFFSET
import com.aibanking.agent.ui.anim.SHIMMER_SWEEP_ANGLE_DEGREES
import com.aibanking.agent.ui.anim.SHIMMER_SWEEP_PERIOD_MILLIS
import com.aibanking.agent.ui.anim.bankShimmerTheme
import com.aibanking.agent.ui.anim.shimmerTransitionSpec
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.lang.reflect.Proxy

class ShimmerTransitionTest {

    @Test
    fun shimmer_constants_match_design_specifications() {
        assertEquals("Shimmer sweep period must be 1200ms", 1200, SHIMMER_SWEEP_PERIOD_MILLIS)
        assertEquals("Shimmer tilt angle must be 45 degrees", 45f, SHIMMER_SWEEP_ANGLE_DEGREES, 0.001f)
        assertEquals("Crossfade duration must be 200ms", 200, SHIMMER_FADE_DURATION_MILLIS)
        assertEquals("Content rise offset must be 8dp", 8.dp, SHIMMER_RISE_OFFSET)
    }

    @Test
    fun bank_shimmer_theme_has_45_degree_tilt_and_light_shader_colors() {
        val lightTheme = bankShimmerTheme(darkTheme = false)
        assertEquals(45f, lightTheme.rotation, 0.001f)
        assertEquals(3, lightTheme.shaderColors.size)
        // Middle color should have highest alpha (peak highlight)
        assertTrue(lightTheme.shaderColors[1].alpha > lightTheme.shaderColors[0].alpha)

        val darkTheme = bankShimmerTheme(darkTheme = true)
        assertEquals(45f, darkTheme.rotation, 0.001f)
        assertEquals(3, darkTheme.shaderColors.size)
        assertTrue(darkTheme.shaderColors[1].alpha > darkTheme.shaderColors[0].alpha)
    }

    @Test
    fun shimmer_transition_spec_produces_valid_transform_on_loading_complete() {
        val density = Density(density = 2f, fontScale = 1f)
        val specLambda = shimmerTransitionSpec(density, motionEnabled = true)

        val scope = createMockTransitionScope(initial = true, target = false)
        val transform = specLambda.invoke(scope)
        assertNotNull(transform)
    }

    @Test
    fun shimmer_transition_spec_supports_motion_disabled() {
        val density = Density(density = 2f, fontScale = 1f)
        val specLambda = shimmerTransitionSpec(density, motionEnabled = false)

        val scope = createMockTransitionScope(initial = true, target = false)
        val transform = specLambda.invoke(scope)
        assertNotNull(transform)
    }

    @Suppress("UNCHECKED_CAST")
    private fun createMockTransitionScope(initial: Boolean, target: Boolean): AnimatedContentTransitionScope<Boolean> {
        return Proxy.newProxyInstance(
            AnimatedContentTransitionScope::class.java.classLoader,
            arrayOf(AnimatedContentTransitionScope::class.java),
        ) { _, method, _ ->
            when (method.name) {
                "getInitialState" -> initial
                "getTargetState" -> target
                else -> null
            }
        } as AnimatedContentTransitionScope<Boolean>
    }
}
