package com.aibanking.agent.ui.chat

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.aibanking.agent.data.NavPointView
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.anim.BankHaptics
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import java.time.LocalDate
import java.util.Locale

@Composable
internal fun WealthChart(product: ProductView, range: String, modifier: Modifier = Modifier) {
    val semantic = LocalBankSemanticColors.current
    val chartSurface = MaterialTheme.colorScheme.surface
    val view = LocalView.current
    val motion = LocalBankMotionEnabled.current
    val all = product.navSeries.filter { it.nav?.isFinite() == true && it.benchmarkNav?.isFinite() == true }
    val points = rangePoints(all, range)
    var selectedIndex by remember(points) { mutableIntStateOf(points.lastIndex.coerceAtLeast(0)) }

    // One-time left-to-right reveal on initial chart composition
    val revealProgress = remember(product.productId) { Animatable(if (motion) 0f else 1f) }
    LaunchedEffect(product.productId, motion) {
        if (motion && revealProgress.value < 1f) {
            revealProgress.animateTo(1f, tween(durationMillis = 500, easing = FastOutSlowInEasing))
        } else {
            revealProgress.snapTo(1f)
        }
    }

    // Smooth Y-interpolation when switching time ranges
    var previousNavNorm by remember(product.productId) { mutableStateOf<List<Float>?>(null) }
    var previousBenchNorm by remember(product.productId) { mutableStateOf<List<Float>?>(null) }
    val currentNorms = remember(points) { normalizeSeries(points) }
    val morphProgress = remember(range) { Animatable(if (motion && previousNavNorm != null) 0f else 1f) }
    LaunchedEffect(range, points, motion) {
        if (motion && previousNavNorm != null && morphProgress.value < 1f) {
            morphProgress.animateTo(1f, tween(durationMillis = 280, easing = FastOutSlowInEasing))
        } else {
            morphProgress.snapTo(1f)
        }
        previousNavNorm = currentNorms.first
        previousBenchNorm = currentNorms.second
    }

    Column(
        modifier.semantics {
            contentDescription = if (points.isEmpty()) "业绩走势图暂无数据" else {
                val point = points[selectedIndex.coerceIn(0, points.lastIndex)]
                "业绩走势图，区间 $range，日期 ${point.date}，产品净值 ${point.nav ?: 0.0}，基准净值 ${point.benchmarkNav ?: 0.0}"
            }
        },
    ) {
        if (points.isEmpty()) {
            Box(
                Modifier.fillMaxWidth().height(160.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(16.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Text("业绩走势图暂无数据", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            return@Column
        }
        if (points.isNotEmpty()) {
            val point = points[selectedIndex.coerceIn(0, points.lastIndex)]
            Row(
                Modifier.fillMaxWidth().padding(bottom = 6.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    "${point.date} · 净值 ${point.nav?.let { "%.4f".format(Locale.CHINA, it) } ?: "—"}",
                    style = MaterialTheme.typography.labelSmall.copy(fontFeatureSettings = "tnum"),
                    fontWeight = FontWeight.SemiBold,
                    color = semantic.chartPrimary,
                )
                Text(
                    "基准 ${point.benchmarkNav?.let { "%.4f".format(Locale.CHINA, it) } ?: "—"}",
                    style = MaterialTheme.typography.labelSmall.copy(fontFeatureSettings = "tnum"),
                    color = semantic.chartBenchmark,
                )
            }
        }
        Canvas(
            Modifier
                .fillMaxWidth()
                .height(160.dp)
                .pointerInput(points) {
                    detectHorizontalDragGestures { change, _ ->
                        change.consume()
                        if (points.size > 1) {
                            val nextIdx = ((change.position.x / size.width) * (points.size - 1)).toInt().coerceIn(0, points.lastIndex)
                            if (nextIdx != selectedIndex) {
                                selectedIndex = nextIdx
                                BankHaptics.tick(view)
                            }
                        }
                    }
                },
        ) {
            if (points.size < 2) return@Canvas
            repeat(4) { line ->
                val gridY = size.height * (line + 1) / 5f
                drawLine(semantic.chartGrid.copy(alpha = .55f), Offset(0f, gridY), Offset(size.width, gridY), strokeWidth = 1.dp.toPx())
            }
            val (targetNavNorm, targetBenchNorm) = currentNorms
            val prevNav = previousNavNorm
            val prevBench = previousBenchNorm
            val morph = morphProgress.value

            fun x(i: Int) = i * size.width / (points.size - 1)
            fun yFromNorm(norm: Float) = size.height * 0.85f - norm * (size.height * 0.70f)
            fun navY(i: Int): Float {
                val target = targetNavNorm.getOrElse(i) { 0.5f }
                val from = if (prevNav != null && morph < 1f) sampleNormalized(prevNav, i, points.size) else target
                return yFromNorm(from + (target - from) * morph)
            }
            fun benchY(i: Int): Float {
                val target = targetBenchNorm.getOrElse(i) { 0.5f }
                val from = if (prevBench != null && morph < 1f) sampleNormalized(prevBench, i, points.size) else target
                return yFromNorm(from + (target - from) * morph)
            }

            clipRect(right = size.width * revealProgress.value) {
                // 绘制产品曲线下的浅红渐变遮罩
                val fillPath = Path().apply {
                    moveTo(x(0), size.height)
                    lineTo(x(0), navY(0))
                    for (i in 1 until points.size) {
                        lineTo(x(i), navY(i))
                    }
                    lineTo(x(points.lastIndex), size.height)
                    close()
                }
                drawPath(
                    fillPath,
                    Brush.verticalGradient(
                        colors = listOf(semantic.chartPrimary.copy(alpha = 0.17f), semantic.chartPrimary.copy(alpha = 0.01f)),
                        startY = yFromNorm(1f),
                        endY = size.height,
                    ),
                )

                // 绘制基准曲线 (灰蓝色)
                for (i in 0 until points.lastIndex) {
                    drawLine(
                        semantic.chartBenchmark,
                        Offset(x(i), benchY(i)),
                        Offset(x(i + 1), benchY(i + 1)),
                        strokeWidth = 3f,
                        cap = StrokeCap.Round,
                    )
                }

                // 绘制本产品曲线 (醒目红)
                for (i in 0 until points.lastIndex) {
                    drawLine(
                        semantic.chartPrimary,
                        Offset(x(i), navY(i)),
                        Offset(x(i + 1), navY(i + 1)),
                        strokeWidth = 5f,
                        cap = StrokeCap.Round,
                    )
                }
            }

            // 最高点标记与虚线指示
            val maxNavIndex = points.indices.maxByOrNull { points[it].nav ?: 0.0 } ?: 0
            val peakX = x(maxNavIndex)
            val peakY = navY(maxNavIndex)
            drawLine(
                semantic.chartPrimary.copy(alpha = 0.5f),
                Offset(peakX, 10f),
                Offset(peakX, peakY),
                strokeWidth = 2f,
            )
            drawCircle(semantic.chartPrimary, 5f, Offset(peakX, peakY))

            // 交互十字准星
            val index = selectedIndex.coerceIn(0, points.lastIndex)
            val scrubX = x(index)
            drawLine(semantic.chartGrid, Offset(scrubX, 0f), Offset(scrubX, size.height), strokeWidth = 2f)
            drawCircle(chartSurface, 7f, Offset(scrubX, navY(index)))
            drawCircle(semantic.chartPrimary, 5f, Offset(scrubX, navY(index)))
            drawCircle(chartSurface, 6f, Offset(scrubX, benchY(index)))
            drawCircle(semantic.chartBenchmark, 4f, Offset(scrubX, benchY(index)))
        }
    }
}

private fun normalizeSeries(points: List<NavPointView>): Pair<List<Float>, List<Float>> {
    if (points.isEmpty()) return emptyList<Float>() to emptyList()
    val min = points.minOf { minOf(it.nav ?: 0.0, it.benchmarkNav ?: 0.0) }
    val max = points.maxOf { maxOf(it.nav ?: 0.0, it.benchmarkNav ?: 0.0) }
    val span = (max - min).takeIf { it > 0.0 } ?: 1.0
    val navNorm = points.map { (((it.nav ?: min) - min) / span).toFloat().coerceIn(0f, 1f) }
    val benchNorm = points.map { (((it.benchmarkNav ?: min) - min) / span).toFloat().coerceIn(0f, 1f) }
    return navNorm to benchNorm
}

private fun sampleNormalized(series: List<Float>, index: Int, targetCount: Int): Float {
    if (series.isEmpty()) return 0.5f
    if (series.size == 1 || targetCount <= 1) return series.first()
    val pos = (index.toFloat() / (targetCount - 1)) * (series.size - 1)
    val lo = pos.toInt().coerceIn(0, series.lastIndex)
    val hi = (lo + 1).coerceIn(0, series.lastIndex)
    val frac = pos - lo
    return series[lo] + (series[hi] - series[lo]) * frac
}

private fun rangePoints(points: List<NavPointView>, range: String): List<NavPointView> {
    if (points.isEmpty()) return emptyList()
    val parsed = points.mapNotNull { point ->
        runCatching { LocalDate.parse(point.date) to point }.getOrNull()
    }
    if (parsed.size != points.size) {
        val fallbackCount = when (range) {
            "近1月" -> 5
            "近3月" -> 10
            else -> points.size
        }.coerceAtLeast(1)
        return points.takeLast(fallbackCount)
    }
    val end = parsed.maxOf { it.first }
    val start = when (range) {
        "近1月" -> end.minusMonths(1)
        "近3月" -> end.minusMonths(3)
        "近6月" -> end.minusMonths(6)
        "近1年" -> end.minusMonths(12)
        "成立以来" -> LocalDate.MIN
        else -> end.minusMonths(12)
    }
    return parsed.filter { it.first >= start && it.first <= end }.map { it.second }.ifEmpty { points.takeLast(1) }
}

@Composable
internal fun returnColor(value: Double?): Color = when {
    value == null || !value.isFinite() || value == 0.0 -> Color.Unspecified
    value > 0.0 -> WealthPositive
    else -> WealthNegative
}
