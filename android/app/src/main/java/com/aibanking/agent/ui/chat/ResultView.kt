package com.aibanking.agent.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import java.util.Locale
import kotlin.math.max

/**
 * Renders the structured half of a turn's result.
 *
 * The reply text is a prose summary; the numbers behind it (balances, category
 * totals, anomaly flags, the task DAG, the birthday memo) arrive as data.  The
 * app shows those facts as facts rather than asking the user to trust a
 * sentence - and it renders decode failures as nothing at all instead of
 * inventing a value, which is the same rule the agent applies server-side.
 */
@Composable
fun ResultExtras(result: Map<String, Any?>?) {
    if (result.isNullOrEmpty()) return
    // A compound read-only request ("查余额，再分析上月账单") produces several
    // execution results; render every one rather than only the first.
    val executions = buildList {
        result.child("execution_result")?.let { add(it) }
        addAll(result.children("execution_results"))
    }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
        executions.forEach { execution ->
            val type = execution.text("type")
            if (type in WEALTH_PRODUCT_TYPES) {
                // 理财回复：产品渲染成与理财页同款的卡片，而不是文本清单。
                execution.child("data")?.let { WealthProducts(it) }
                return@forEach
            }
            execution.child("facts")?.let { BillFacts(it) }
            execution.child("data")?.let { KeyValues(it, title = "核心返回") }
        }
        result.child("profile_result")?.let { RiskProfileCard(it) }
        result.child("todo_card")?.let { TodoCard(it) }
        result.child("plan")?.let { PlanCard(it) }
    }
}

/** 测评结果卡：按等级配色的彩色气泡，替代文本三行说明。 */
@Composable
private fun RiskProfileCard(profile: Map<String, Any?>) {
    val grade = profile.text("grade") ?: return
    val typeName = profile.text("type") ?: ""
    val traits = profile.text("traits") ?: ""
    val levels = profile.text("matching_levels") ?: ""
    val accent = when (grade) {
        "C1" -> Color(0xFF1565C0)
        "C2" -> Color(0xFF2E7D32)
        "C3" -> Color(0xFFE65100)
        "C4" -> Color(0xFFC62828)
        else -> Color(0xFF6A1B9A)
    }
    Card(
        colors = CardDefaults.cardColors(containerColor = accent.copy(alpha = 0.12f)),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.fillMaxWidth().padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Box(
                    modifier = Modifier
                        .background(accent, RoundedCornerShape(10.dp))
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                ) {
                    Text(
                        grade,
                        color = Color.White,
                        fontWeight = FontWeight.Bold,
                        style = MaterialTheme.typography.titleLarge,
                    )
                }
                Text(
                    typeName,
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold,
                    color = accent,
                )
            }
            if (traits.isNotBlank()) Text("特点：$traits", style = MaterialTheme.typography.bodyMedium)
            if (levels.isNotBlank()) Text("匹配理财产品级别：$levels", style = MaterialTheme.typography.bodySmall)
            Text(
                "测评结果已记录；重做测评有次数限制（当日 2 次、近 1 月 4 次、近 1 年 8 次）。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private val WEALTH_PRODUCT_TYPES = setOf("WEALTH_QUERY", "WEALTH_RECOMMEND", "WEALTH_COMPARE")

@Composable
private fun WealthProducts(data: Map<String, Any?>) {
    val products = data.children("data")
    if (products.isEmpty()) return

    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        SectionTitle("理财产品（已核验）")
        products.forEach { product ->
            val name = product.text("name") ?: return@forEach
            val level = product.text("risk_level") ?: "—"
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.fillMaxWidth().padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                        product.text("category")?.let { CategoryTag(it) }
                        RiskTag(level)
                    }
                    Text(name, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    Text(
                        product.text("benchmark") ?: "业绩比较基准以产品说明书为准",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.Bold,
                    )
                    val minAmount = product.number("min_amount")
                    val term = product.text("term")
                    Text(
                        buildString {
                            if (minAmount != null) append("起购 " + money(minAmount))
                            term?.let { append(if (isEmpty()) it else " · $it") }
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun CategoryTag(text: String) {
    Box(
        modifier = Modifier
            .background(MaterialTheme.colorScheme.secondaryContainer, RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    ) {
        Text(text, style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun RiskTag(level: String) {
    val color = when (level) {
        "R1" -> Color(0xFF2E7D32)
        "R2" -> Color(0xFF1565C0)
        "R3" -> Color(0xFFE65100)
        else -> Color(0xFFC62828)
    }
    Box(
        modifier = Modifier
            .background(color, RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    ) {
        Text(level, style = MaterialTheme.typography.labelSmall, color = Color.White, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleSmall)
}

@Composable
private fun BillFacts(facts: Map<String, Any?>) {
    val spent = facts.money("total_spent")
    val refunds = facts.money("refund_total")
    val net = facts.money("net_spent")
    val count = facts.text("record_count")
    val engine = facts.text("analysis_engine")
    if (spent == null && count == null) return

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            SectionTitle("账单事实（已核验）")
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                count?.let { Metric("笔数", it) }
                spent?.let { Metric("支出", it) }
                refunds?.let { Metric("退款", it) }
                net?.let { Metric("净支出", it) }
            }
            // Stating where the aggregation ran keeps the "the model may phrase
            // facts but never invent them" claim checkable.
            engine?.let {
                Text("聚合引擎：$it", style = MaterialTheme.typography.labelSmall)
            }
            CategoryBars(facts)
            Anomalies(facts)
        }
    }
}

@Composable
private fun Metric(label: String, value: String) {
    Column {
        Text(label, style = MaterialTheme.typography.labelSmall)
        Text(value, style = MaterialTheme.typography.titleSmall)
    }
}

@Composable
private fun CategoryBars(facts: Map<String, Any?>) {
    val rows = facts.children("category_breakdown")
    if (rows.isEmpty()) return
    val largest = rows.mapNotNull { it.number("amount") }.maxOrNull() ?: return
    if (largest <= 0.0) return

    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        SectionTitle("分类占比")
        rows.forEach { row ->
            val category = row.text("category") ?: return@forEach
            val amount = row.number("amount") ?: return@forEach
            val share = row.number("share")
            Column {
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(category, style = MaterialTheme.typography.bodySmall)
                    Text(
                        buildString {
                            append(money(amount))
                            if (share != null) append("（${"%.1f".format(Locale.CHINA, share * 100)}%）")
                        },
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 2.dp)
                        .height(6.dp)
                        .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(3.dp)),
                ) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth(max(0.02f, (amount / largest).toFloat().coerceIn(0f, 1f)))
                            .height(6.dp)
                            .background(MaterialTheme.colorScheme.primary, RoundedCornerShape(3.dp)),
                    )
                }
            }
        }
    }
}

@Composable
private fun Anomalies(facts: Map<String, Any?>) {
    val rows = facts.children("anomalies")
    if (rows.isEmpty()) return
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        SectionTitle("需复核交易")
        rows.forEach { row ->
            val id = row.text("tx_id") ?: "—"
            val merchant = row.text("merchant") ?: "—"
            val amount = row.number("amount")?.let { money(it) } ?: "—"
            val reasons = (row["reasons"] as? List<*>)?.joinToString("、") { it.toString() } ?: ""
            Text(
                "• $id · $merchant · $amount · $reasons",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@Composable
private fun KeyValues(data: Map<String, Any?>, title: String) {
    val scalars = data.entries
        .filter { it.value is Number || it.value is String || it.value is Boolean }
        .take(8)
    if (scalars.isEmpty()) return

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            SectionTitle(title)
            scalars.forEach { (key, value) ->
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(key, style = MaterialTheme.typography.bodySmall)
                    Text(
                        value?.let { if (it is Number) money(it.toDouble()) else it.toString() } ?: "—",
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Monospace,
                    )
                }
            }
        }
    }
}

@Composable
private fun TodoCard(card: Map<String, Any?>) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            SectionTitle(card.text("title") ?: "待办任务")
            card.text("locked_fund")?.let { Text("锁定资产：$it", style = MaterialTheme.typography.bodySmall) }
            card.text("schedule")?.let { Text("执行计划：$it", style = MaterialTheme.typography.bodySmall) }
            card.text("status")?.let { Text("状态：$it", style = MaterialTheme.typography.bodySmall) }
            card.text("task_id")?.let { Text("任务编号：$it", style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace) }
            (card["order_task_ids"] as? List<*>)?.takeIf { it.isNotEmpty() }?.let {
                Text("订单草稿：${it.joinToString("、")}", style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace)
            }
        }
    }
}

@Composable
private fun PlanCard(plan: Map<String, Any?>) {
    val tasks = plan.children("tasks")
    if (tasks.isEmpty()) return
    var expanded by remember { mutableStateOf(false) }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SectionTitle("任务 DAG（${plan.text("intent") ?: "—"}）")
                TextButton(onClick = { expanded = !expanded }) { Text(if (expanded) "收起" else "展开") }
            }
            if (expanded) {
                tasks.forEach { task ->
                    val deps = (task["depends_on"] as? List<*>)?.joinToString(",") { it.toString() }.orEmpty()
                    Text(
                        "${task.text("task_id")} · ${task.text("action")} · ${task.text("risk_level")} · ${task.text("status")}" +
                            if (deps.isNotEmpty()) " ← $deps" else "",
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Monospace,
                    )
                }
                plan.text("policy_version")?.let {
                    Text("策略版本：$it", style = MaterialTheme.typography.labelSmall)
                }
            }
        }
    }
}

// -- defensive decoding ----------------------------------------------------
//
// Gson hands back plain maps with Double/String/Boolean/List values.  Every
// accessor below returns null rather than guessing, so a payload shape change
// shows up as a missing row instead of a wrong number.

private fun Map<String, Any?>.child(key: String): Map<String, Any?>? {
    val value = this[key] as? Map<*, *> ?: return null
    return value.entries.associate { it.key.toString() to it.value }
}

private fun Map<String, Any?>.children(key: String): List<Map<String, Any?>> {
    val value = this[key] as? List<*> ?: return emptyList()
    return value.mapNotNull { item ->
        (item as? Map<*, *>)?.entries?.associate { it.key.toString() to it.value }
    }
}

private fun Map<String, Any?>.text(key: String): String? = this[key]?.toString()

private fun Map<String, Any?>.number(key: String): Double? = (this[key] as? Number)?.toDouble()

private fun Map<String, Any?>.money(key: String): String? = number(key)?.let { money(it) }

private fun money(value: Double): String = "¥" + String.format(Locale.CHINA, "%,.2f", value)
