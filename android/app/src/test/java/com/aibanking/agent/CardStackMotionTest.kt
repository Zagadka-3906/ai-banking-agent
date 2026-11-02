package com.aibanking.agent

import androidx.compose.animation.core.Spring
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.anim.CardStackMotion
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CardStackMotionTest {

    @Test
    fun singleCard_alwaysFullScaleAndZeroOffsets() {
        val collapsed = CardStackMotion.computeParams(index = 0, totalCount = 1, progress = 0f)
        assertEquals(1.0f, collapsed.scale, 0.001f)
        assertEquals(0.dp, collapsed.translationXDp)
        assertEquals(1.0f, collapsed.alpha, 0.001f)

        val expanded = CardStackMotion.computeParams(index = 0, totalCount = 1, progress = 1f)
        assertEquals(1.0f, expanded.scale, 0.001f)
        assertEquals(0.dp, expanded.translationXDp)
    }

    @Test
    fun multiCard_collapsedState_expressesDepthWithScaleAndZIndex() {
        val total = 3
        val card0 = CardStackMotion.computeParams(index = 0, totalCount = total, progress = 0f)
        val card1 = CardStackMotion.computeParams(index = 1, totalCount = total, progress = 0f)
        val card2 = CardStackMotion.computeParams(index = 2, totalCount = total, progress = 0f)

        // 顶层卡片为全比例 scale = 1.0f
        assertEquals(1.0f, card0.scale, 0.001f)
        // 后面卡片依次递减，表现纵深层次
        assertEquals(0.94f, card1.scale, 0.001f)
        assertEquals(0.88f, card2.scale, 0.001f)

        // 前后 zIndex 关系：前景卡片 zIndex 最高
        assertTrue(card0.zIndex > card1.zIndex)
        assertTrue(card1.zIndex > card2.zIndex)

        // 顶层留白足够露边
        val topPadding = CardStackMotion.computeStackTopPadding(totalCount = total, progress = 0f)
        assertEquals(56.dp, topPadding)
    }

    @Test
    fun multiCard_expandedState_restoresFullScaleAndAligns() {
        val total = 3
        val card0 = CardStackMotion.computeParams(index = 0, totalCount = total, progress = 1f)
        val card1 = CardStackMotion.computeParams(index = 1, totalCount = total, progress = 1f)
        val card2 = CardStackMotion.computeParams(index = 2, totalCount = total, progress = 1f)

        // 展开后所有卡片恢复 1.0f 缩放与 0 偏移
        assertEquals(1.0f, card0.scale, 0.001f)
        assertEquals(1.0f, card1.scale, 0.001f)
        assertEquals(1.0f, card2.scale, 0.001f)

        assertEquals(0.dp.value, card0.translationXDp.value, 0.001f)
        assertEquals(0.dp.value, card1.translationXDp.value, 0.001f)
        assertEquals(0.dp.value, card2.translationXDp.value, 0.001f)

        val topPadding = CardStackMotion.computeStackTopPadding(totalCount = total, progress = 1f)
        assertEquals(0.dp.value, topPadding.value, 0.001f)
    }

    @Test
    fun springSpecification_usesMediumBouncyAndLowStiffness() {
        val spec = CardStackMotion.StackSpringSpec
        assertEquals(Spring.DampingRatioMediumBouncy, spec.dampingRatio, 0.001f)
        assertEquals(Spring.StiffnessLow, spec.stiffness, 0.001f)
    }

    @Test
    fun midTransition_producesStaggerFlourish() {
        val total = 3
        val mid1 = CardStackMotion.computeParams(index = 1, totalCount = total, progress = 0.5f)
        val mid2 = CardStackMotion.computeParams(index = 2, totalCount = total, progress = 0.5f)

        // 中间状态左右错位排开
        assertTrue(mid1.translationXDp.value > 0f)
        assertTrue(mid2.translationXDp.value < 0f)
    }
}
