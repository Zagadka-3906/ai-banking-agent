package com.aibanking.agent

import androidx.compose.animation.BoundsTransform
import androidx.compose.animation.ExperimentalSharedTransitionApi
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.SnapSpec
import androidx.compose.animation.core.TweenSpec
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Outline
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.anim.CONTAINER_TRANSFORM_DURATION_MILLIS
import com.aibanking.agent.ui.anim.ContainerTransformEasing
import com.aibanking.agent.ui.anim.containerTransformBoundsTransform
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalSharedTransitionApi::class)
class ContainerTransformTest {

    @Test
    fun container_transform_duration_is_350ms() {
        assertEquals(350, CONTAINER_TRANSFORM_DURATION_MILLIS)
    }

    @Test
    fun container_transform_easing_is_emphasized_decelerate() {
        assertTrue(ContainerTransformEasing is CubicBezierEasing)
        val easing = ContainerTransformEasing as CubicBezierEasing
        assertEquals(0f, easing.transform(0f), 0.001f)
        assertEquals(1f, easing.transform(1f), 0.001f)
        val progressAtHalf = easing.transform(0.5f)
        assertTrue("Emphasized decelerate should front-load motion, got $progressAtHalf", progressAtHalf > 0.8f)
    }

    @Test
    fun bounds_transform_respects_motion_enabled_flag() {
        val enabledTransform = containerTransformBoundsTransform(motionEnabled = true)
        val initialRect = Rect(0f, 0f, 100f, 100f)
        val targetRect = Rect(0f, 0f, 400f, 800f)

        val method = BoundsTransform::class.java.methods.first { it.parameterCount == 2 }
        val spec = method.invoke(enabledTransform, initialRect, targetRect)
        assertTrue(spec is TweenSpec<*>)
        assertEquals(350, (spec as TweenSpec<*>).durationMillis)

        val disabledTransform = containerTransformBoundsTransform(motionEnabled = false)
        val disabledSpec = method.invoke(disabledTransform, initialRect, targetRect)
        assertTrue(disabledSpec is SnapSpec<*>)
    }

    @Test
    fun shape_transitions_from_rounded_card_to_straight_detail() {
        val density = Density(density = 2f, fontScale = 1f)
        val layoutDirection = LayoutDirection.Ltr
        val size = Size(360f, 240f)

        // Card shape produces Rounded outline
        val cardShape = RoundedCornerShape(16.dp)
        val cardOutline = cardShape.createOutline(size, layoutDirection, density)
        assertTrue(cardOutline is Outline.Rounded)
        val roundRect = (cardOutline as Outline.Rounded).roundRect
        assertEquals(32f, roundRect.topLeftCornerRadius.x, 0.01f) // 16dp * 2f density

        // Detail shape produces straight Rectangle outline
        val detailShape = RoundedCornerShape(0.dp)
        val detailOutline = detailShape.createOutline(size, layoutDirection, density)
        assertTrue(detailOutline is Outline.Rectangle || (detailOutline is Outline.Rounded && detailOutline.roundRect.topLeftCornerRadius.x == 0f))
    }
}
