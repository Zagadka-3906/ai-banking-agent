package com.aibanking.agent.ui.chat

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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import java.time.LocalDate
import java.util.Locale

@Composable
internal fun WealthChart(product: ProductView, range: String, modifier: Modifier = Modifier) {
    val semantic = LocalBankSemanticColors.current
    val chartSurface = MaterialTheme.colorScheme.surface
    val all = product.navSeries.filter { it.nav?.isFinite() == true && it.benchmarkNav?.isFinite() == true }
    val points = rangePoints(all, range)
    var selectedIndex by remember(points) { mutableIntStateOf(points.lastIndex.coerceAtLeast(0)) }

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
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = semantic.chartPrimary,
                )
                Text(
                    "基准 ${point.benchmarkNav?.let { "%.4f".format(Locale.CHINA, it) } ?: "—"}",
                    style = MaterialTheme.typography.labelSmall,
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
                            selectedIndex = ((change.position.x / size.width) * (points.size - 1)).toInt().coerceIn(0, points.lastIndex)
                        }
                    }
                }
        ) {
            if (points.size < 2) return@Canvas
            repeat(4) { line ->
                val gridY = size.height * (line + 1) / 5f
                drawLine(semantic.chartGrid.copy(alpha = .55f), Offset(0f, gridY), Offset(size.width, gridY), strokeWidth = 1.dp.toPx())
            }
            val min = points.minOf { minOf(it.nav!!, it.benchmarkNav!!) }
            val max = points.maxOf { maxOf(it.nav!!, it.benchmarkNav!!) }
            val span = (max - min).takeIf { it > 0.0 } ?: 1.0
            fun x(i: Int) = i * size.width / (points.size - 1)
            fun y(value: Double) = size.height * 0.85f - ((value - min) / span * (size.height * 0.70f)).toFloat()

            // 绘制产品曲线下的浅红渐变遮罩
            val fillPath = Path().apply {
                moveTo(x(0), size.height)
                lineTo(x(0), y(points[0].nav!!))
                for (i in 1 until points.size) {
                    lineTo(x(i), y(points[i].nav!!))
                }
                lineTo(x(points.lastIndex), size.height)
                close()
            }
            drawPath(
                fillPath,
                Brush.verticalGradient(
                    colors = listOf(semantic.chartPrimary.copy(alpha = 0.17f), semantic.chartPrimary.copy(alpha = 0.01f)),
                    startY = y(max),
                    endY = size.height,
                ),
            )

            // 绘制基准曲线 (灰蓝色)
            for (i in 0 until points.lastIndex) {
                drawLine(
                    semantic.chartBenchmark,
                    Offset(x(i), y(points[i].benchmarkNav!!)),
                    Offset(x(i + 1), y(points[i + 1].benchmarkNav!!)),
                    strokeWidth = 3f,
                    cap = StrokeCap.Round,
                )
            }

            // 绘制本产品曲线 (醒目红)
            for (i in 0 until points.lastIndex) {
                drawLine(
                    semantic.chartPrimary,
                    Offset(x(i), y(points[i].nav!!)),
                    Offset(x(i + 1), y(points[i + 1].nav!!)),
                    strokeWidth = 5f,
                    cap = StrokeCap.Round,
                )
            }

            // 最高点标记与虚线指示
            val maxNavIndex = points.indices.maxByOrNull { points[it].nav ?: 0.0 } ?: 0
            val peakX = x(maxNavIndex)
            val peakY = y(points[maxNavIndex].nav!!)
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
            drawCircle(chartSurface, 7f, Offset(scrubX, y(points[index].nav!!)))
            drawCircle(semantic.chartPrimary, 5f, Offset(scrubX, y(points[index].nav!!)))
            drawCircle(chartSurface, 6f, Offset(scrubX, y(points[index].benchmarkNav!!)))
            drawCircle(semantic.chartBenchmark, 4f, Offset(scrubX, y(points[index].benchmarkNav!!)))
        }
    }
}

private fun rangePoints(points: List<com.aibanking.agent.data.NavPointView>, range: String): List<com.aibanking.agent.data.NavPointView> {
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
