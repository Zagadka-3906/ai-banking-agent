package com.aibanking.agent.ui.chat

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aibanking.agent.ui.theme.BankPalette
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.components.*
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import java.util.Locale

/** Render returned facts without fabricating defaults or deriving financial outcomes. */
@Composable
fun ResultExtras(result: Map<String, Any?>?) {
    if (result.isNullOrEmpty()) return
    val executions = buildList {
        result.child("execution_result")?.let { add(it) }
        addAll(result.children("execution_results"))
    }.distinct()
    Column(verticalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
        // The level comes before the products it qualifies.
        result.child("profile_result")?.let { RiskProfileCard(it) }
        executions.forEach { execution ->
            val type = execution.text("type")
            // These replies already render their verified facts in the dialogue.
            // Transport fields (user_id, policy version, topic) are not extra cards.
            if (type == "DISPUTE_QUERY" || type == "BANKING_HELP") return@forEach
            if (type in WEALTH_PRODUCT_TYPES) {
                execution.child("data")?.let { WealthProducts(it, type) }
                return@forEach
            }
            if (type == "WEALTH_HOLDINGS") {
                execution.child("data")?.let { WealthHoldings(it) }
                return@forEach
            }
            execution.child("facts")?.let { BillFacts(it) }
            execution.child("data")?.let { data ->
                if (type == "FRAUD_DISPUTE") FraudProtectionCard(data)
                else KeyValues(data, title = executionTitle(type))
            }
        }
        result.child("todo_card")?.let { TodoCard(it) }
    }
}

@Composable
private fun RiskProfileCard(profile: Map<String, Any?>) {
    val grade = profile.text("grade") ?: return
    val typeName = profile.text("type").orEmpty()
    val traits = profile.text("traits").orEmpty()
    val levels = profile.text("matching_levels").orEmpty()
    val semantic = LocalBankSemanticColors.current
    val accent = when (grade) {
        "C1", "C2" -> semantic.riskGreenContainer
        "C3" -> semantic.riskYellowContainer
        "C4", "C5" -> semantic.riskRedContainer
        else -> MaterialTheme.colorScheme.surfaceContainerHighest
    }
    ReplyPanel {
        ResultCardTitle("您的风险测评", BankIconKind.Shield)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Surface(shape = CircleShape, color = accent, contentColor = semantic.onRiskContainer) {
                Text(grade, Modifier.padding(16.dp), fontWeight = FontWeight.Bold, style = MaterialTheme.typography.headlineSmall)
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(typeName, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                if (levels.isNotBlank()) Text("匹配理财产品级别：$levels", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        if (traits.isNotBlank()) Text("特点：$traits", style = MaterialTheme.typography.bodyMedium)
        ReplyDivider(color = MaterialTheme.colorScheme.outlineVariant)
        Text(
            "测评结果已记录；重做测评有次数限制（当日 2 次、近 1 月 4 次、近 1 年 8 次）。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

private val WEALTH_PRODUCT_TYPES = setOf("WEALTH_QUERY", "WEALTH_RECOMMEND", "WEALTH_COMPARE")

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WealthProducts(data: Map<String, Any?>, type: String?) {
    val products = data.children("data")
    if (products.isEmpty()) return
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        ResultCardTitle(when (type) {
            "WEALTH_RECOMMEND" -> "与您匹配的产品"
            "WEALTH_COMPARE" -> "产品对比"
            else -> "在售理财产品"
        }, BankIconKind.Chart)
        products.forEach { product ->
            val name = product.text("name") ?: return@forEach
            ReplyPanel {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    // The code is what "申购 LX00118 500 元" needs, so the card shows it.
                    product.text("product_id")?.let { BankStatusChip(it, BankSemanticStatus.Neutral) }
                    product.text("category")?.let { BankStatusChip(it, BankSemanticStatus.Neutral) }
                    RiskBadge(product.text("risk_level") ?: "—")
                }
                Text(name, style = MaterialTheme.typography.titleMedium)
                Text(
                    product.text("benchmark") ?: "业绩比较基准以产品说明书为准",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.secondary,
                    fontWeight = FontWeight.SemiBold,
                )
                val minAmount = product.number("min_amount")
                val term = product.text("term")
                Text(
                    listOfNotNull(minAmount?.let { "起购 " + money(it) }, term).joinToString(" · "),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/** One panel per holding: product, code, principal and indicative value. */
@Composable
private fun WealthHoldings(data: Map<String, Any?>) {
    val holdings = data.children("data")
    if (holdings.isEmpty()) return
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        ResultCardTitle("我的理财持仓", BankIconKind.Wallet)
        holdings.mapNotNull { it.number("principal") }.takeIf { it.isNotEmpty() }?.let { principals ->
            ReplyValueHero("本金合计", money(principals.sum()))
        }
        holdings.forEach { holding ->
            ReplyPanel {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    holding.text("product_id")?.let { BankStatusChip(it, BankSemanticStatus.Neutral) }
                    holding.text("holding_id")?.let { BankStatusChip("持仓 $it", BankSemanticStatus.Neutral) }
                }
                Text(holding.text("product_name") ?: "理财产品", style = MaterialTheme.typography.titleMedium)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    holding.money("principal")?.let { Metric("本金", it, Modifier.weight(1f)) }
                    holding.money("estimated_value")?.let { Metric("参考市值", it, Modifier.weight(1f)) }
                }
                holding.text("purchased_at")?.take(10)?.let {
                    Text("申购于 $it · 市值为演示估算，以产品结算为准", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
}

@Composable
private fun ResultCardTitle(text: String, icon: BankIconKind) {
    Row(
        Modifier.fillMaxWidth().padding(bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        ReplyIconTile(icon)
        Text(text, Modifier.weight(1f), style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BillFacts(facts: Map<String, Any?>) {
    val spent = facts.money("total_spent")
    val refunds = facts.money("refund_total")
    val net = facts.money("net_spent")
    val count = facts.number("record_count")?.let { String.format(Locale.CHINA, "%,.0f", it) } ?: facts.text("record_count")
    if (spent == null && count == null) return
    ReplyPanel {
        ResultCardTitle("账单概览", BankIconKind.Chart)
        val metrics = listOfNotNull(
            count?.let { "笔数" to it }, spent?.let { "支出" to it },
            refunds?.let { "退款" to it }, net?.let { "净支出" to it },
        )
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val columns = if (maxWidth < 240.dp) 1 else 2
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                metrics.chunked(columns).forEach { row ->
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        row.forEach { (label, value) -> Metric(label, value, Modifier.weight(1f)) }
                        if (row.size < columns) Spacer(Modifier.weight(1f))
                    }
                }
            }
        }
        CategoryBars(facts)
        Anomalies(facts)
        facts.text("analysis_engine")?.let {
            Text("聚合引擎：$it", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun Metric(label: String, value: String, modifier: Modifier = Modifier) {
    Surface(modifier, shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(value, style = MaterialTheme.typography.titleMedium.copy(fontFeatureSettings = "tnum"),
                fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
        }
    }
}

@Composable
private fun CategoryBars(facts: Map<String, Any?>) {
    val rows = facts.children("category_breakdown")
    val largest = rows.mapNotNull { it.number("amount") }.maxOrNull() ?: return
    if (largest <= 0.0) return
    val motion = LocalBankMotionEnabled.current
    Column(Modifier.padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        SectionTitle("分类占比")
        rows.forEach { row ->
            val category = row.text("category") ?: return@forEach
            val amount = row.number("amount") ?: return@forEach
            val share = row.number("share")
            var visible by remember(category, amount) { mutableStateOf(false) }
            LaunchedEffect(category, amount) { visible = true }
            val fraction by animateFloatAsState(
                targetValue = if (visible || !motion) (amount / largest).toFloat().coerceIn(0f, 1f) else 0f,
                animationSpec = tween(if (motion) 380 else 0),
                label = "categoryBar",
            )
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(category, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium)
                Text(
                    buildString {
                        append(money(amount))
                        if (share != null) append("（${"%.1f".format(Locale.CHINA, share * 100)}%）")
                    },
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Box(Modifier.fillMaxWidth().height(8.dp).background(MaterialTheme.colorScheme.surfaceContainerHighest, CircleShape)) {
                    Box(Modifier.fillMaxWidth(fraction).fillMaxHeight()
                        .background(LocalBankSemanticColors.current.brandGradient, CircleShape))
                }
            }
        }
    }
}

@Composable
private fun Anomalies(facts: Map<String, Any?>) {
    val rows = facts.children("anomalies")
    if (rows.isEmpty()) return
    val accent = LocalBankSemanticColors.current.riskYellow
    Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.tertiaryContainer) {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                BankIcon(BankIconKind.Shield, Modifier.size(18.dp), tint = accent)
                BankStatusChip("需复核交易", BankSemanticStatus.RiskYellow)
            }
            rows.forEach { row ->
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(
                        listOfNotNull(row.text("merchant"), row.number("amount")?.let(::money)).joinToString(" · ").ifBlank { "交易资料待补充" },
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    row.text("tx_id")?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    (row["reasons"] as? List<*>)?.takeIf { it.isNotEmpty() }?.let {
                        Text(it.joinToString("、"), style = MaterialTheme.typography.bodySmall, color = accent)
                    }
                }
            }
        }
    }
}

@Composable
private fun FraudProtectionCard(payload: Map<String, Any?>) {
    val data = payload.child("data") ?: payload
    val case = data.child("dispute_case")
    val card = data.child("card")
    if (case == null && card == null) {
        KeyValues(data, "反诈处理结果")
        return
    }
    ReplyPanel {
        ResultCardTitle("反诈保护进展", BankIconKind.Shield)
        card?.text("status")?.let { status ->
            BankStatusChip(
                if (status == "FROZEN") "关联卡片已冻结" else displayStatus(status),
                if (status == "FROZEN") BankSemanticStatus.RiskGreen else BankSemanticStatus.Neutral,
            )
        }
        case?.text("status")?.let { Text(if (it == "PENDING_REVIEW") "争议工单已登记，状态为待人工审核" else displayStatus(it), style = MaterialTheme.typography.bodyMedium) }
        case?.text("case_id")?.let { FactRow("工单编号", it) }
        data.child("transaction")?.let { transaction ->
            transaction.text("merchant")?.let { FactRow("交易商户", it) }
            transaction.money("amount")?.let { FactRow("交易金额", it) }
        }
        if (data.text("status") == "CARD_PROTECTED" && case?.text("status") == "PENDING_REVIEW") {
            Text("本演示没有真实审核队列或预计完成时间，未执行退款。", style = MaterialTheme.typography.bodySmall, color = LocalBankSemanticColors.current.riskYellow)
        }
    }
}

@Composable
private fun KeyValues(data: Map<String, Any?>, title: String) {
    val payload = data.child("data") ?: data
    val receiptTitle = if (title == "处理结果") executionTitle(payload.text("operation") ?: data.text("operation")) else title
    val receipt = buildMap<String, Any?> {
        putAll(payload)
        // Keep useful nested receipt facts without exposing internal card references.
        payload.child("card")?.let { card ->
            listOf("card_id", "number_masked", "daily_limit", "label").forEach { key ->
                if (!containsKey(key)) card[key]?.let { put(key, it) }
            }
        }
        payload.child("product")?.let { product ->
            product["product_id"]?.let { put("product_id", it) }
            product["name"]?.let { put("product_name", it) }
        }
        payload.child("holding")?.let { holding ->
            holding["holding_id"]?.let { put("holding_id", it) }
            if (!containsKey("amount")) holding["principal"]?.let { put("amount", it) }
        }
        payload.child("subscription")?.let { subscription ->
            listOf("merchant", "subscription_id").forEach { key ->
                if (!containsKey(key)) subscription[key]?.let { put(key, it) }
            }
        }
    }
    // Settlement fields may follow metadata in insertion order; never truncate a returned receipt.
    // `code` is the transport envelope's HTTP status: when a payload's `data` is a
    // list the whole envelope lands here, and it is not a fact about the customer.
    val scalars = receipt.entries.filter {
        (it.value is Number || it.value is String || it.value is Boolean) &&
            it.key != "code" &&
            !(it.key == "operation" && receiptTitle != "处理结果")
    }
    if (scalars.isEmpty()) return
    // Highlight one returned amount without hiding any of the other receipt fields.
    val highlighted = PRIMARY_AMOUNT_FIELDS.firstNotNullOfOrNull { key ->
        scalars.firstOrNull { it.key == key && it.value is Number && (it.value as Number).toDouble().isFinite() }
    }
    ReplyPanel {
        ResultCardTitle(receiptTitle, BankIconKind.Bank)
        highlighted?.let { (key, value) ->
            ReplyValueHero(FIELD_LABELS[key] ?: key, formatValue(key, value))
        }
        val status = scalars.firstOrNull { it.key == "status" && it.value is String }?.value as? String
        if (status != null) ReplyStatusChip(status, displayStatus(status))
        val details = scalars.filter { it.key != highlighted?.key && !(it.key == "status" && status != null) }
        details.forEachIndexed { index, (key, value) ->
            if (index > 0) ReplyDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))
            FactRow(FIELD_LABELS[key] ?: key, formatValue(key, value), monetary = key in MONEY_FIELDS, identifier = key.endsWith("_id"))
        }
    }
}

@Composable
private fun FactRow(label: String, value: String, monetary: Boolean = false, identifier: Boolean = false) {
    val valueStyle = MaterialTheme.typography.bodyMedium.copy(
        fontWeight = if (monetary) FontWeight.SemiBold else FontWeight.Normal,
        fontFamily = if (identifier) FontFamily.Monospace else null,
        fontFeatureSettings = if (monetary) "tnum" else null,
    )
    val valueColor = if (monetary) MaterialTheme.colorScheme.secondary else MaterialTheme.colorScheme.onSurface
    BoxWithConstraints(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        if (maxWidth >= 240.dp && value.length <= 20 && !identifier) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(14.dp), verticalAlignment = Alignment.Top) {
                Text(label, Modifier.weight(0.4f), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(value, Modifier.weight(0.6f), style = valueStyle, color = valueColor, textAlign = TextAlign.End)
            }
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
                Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(value, style = valueStyle, color = valueColor)
            }
        }
    }
}

@Composable
private fun TodoCard(card: Map<String, Any?>) {
    ReplyPanel {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            BankIcon(BankIconKind.Check, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.secondary)
            Text(card.text("title") ?: "待办任务", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
        }
        card.text("locked_fund")?.let { FactRow("锁定资产", it) }
        card.text("schedule")?.let { FactRow("执行计划", it) }
        card.text("status")?.let { ReplyStatusChip(it, displayStatus(it)) }
        card.text("task_id")?.let { Text("任务编号：$it", style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        (card["order_task_ids"] as? List<*>)?.takeIf { it.isNotEmpty() }?.let {
            Text("订单草稿：${it.joinToString("、")}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun executionTitle(type: String?): String = when (type) {
    "BALANCE" -> "账户余额"
    "TRANSFER_SUCCESS" -> "转账回执"
    "LOCK_SUCCESS" -> "资金安排"
    "CARD_QUERY" -> "银行卡信息"
    "CARD_APPLY" -> "银行卡申请"
    "CARD_CHANNEL" -> "交易渠道"
    "UNDO_OPERATION" -> "撤销操作"
    "CARD_LIMIT" -> "银行卡限额调整"
    "CARD_FREEZE" -> "银行卡冻结"
    "CARD_UNFREEZE" -> "银行卡解冻"
    "CARD_REPORT_LOST" -> "银行卡挂失"
    "CARD_UNREPORT_LOST" -> "银行卡解除挂失"
    "SUBSCRIPTION_QUERY" -> "订阅信息"
    "SUBSCRIPTION_CANCEL" -> "取消订阅"
    "SUBSCRIPTION_REMINDER" -> "续费提醒"
    "SCHEDULED_TRANSFER" -> "预约转账"
    "SPLIT_BILL" -> "AA 分摊"
    "WEALTH_PURCHASE" -> "理财申购"
    "WEALTH_REDEEM" -> "理财赎回"
    "PASSWORD_CHANGE" -> "密码修改"
    else -> "处理结果"
}

private val FIELD_LABELS = mapOf(
    "total_balance" to "账户总余额", "available_balance" to "可用余额", "locked_amount" to "锁定资金",
    "remaining_available" to "剩余可用余额", "sender_balance" to "账户余额", "transferred_amount" to "转账金额",
    "receiver_name" to "收款人", "transaction_id" to "流水号", "status" to "状态", "task_id" to "任务编号",
    "lock_id" to "资金安排编号", "amount" to "金额", "daily_limit" to "每日限额", "scheduled_at" to "执行时间",
    "collected_amount" to "已收金额", "outstanding_amount" to "待收金额", "per_person" to "人均金额",
    "redeemed_amount" to "赎回金额", "initiator_share" to "发起人分摊金额", "released_amount" to "已释放金额",
    "count" to "数量", "risk_profile" to "风险等级", "disclaimer" to "说明", "message" to "结果说明",
    "card_id" to "卡片编号", "user_id" to "账户标识", "currency" to "币种",
    "operation" to "业务类型", "number_masked" to "卡号", "last4" to "卡片尾号", "label" to "卡片名称",
    "product_id" to "产品编号", "product_name" to "理财产品", "holding_id" to "持仓编号",
    "merchant" to "商户", "subscription_id" to "订阅编号", "target" to "收款对象",
    "schedule_id" to "预约编号", "split_id" to "分摊编号", "participants_total" to "参与人数",
    "created_at" to "创建时间", "collected_at" to "收款时间", "risk_level" to "风险等级",
    "user_name" to "户名", "card_no" to "卡号", "phone" to "手机号",
)
private val MONEY_FIELDS = setOf(
    "total_balance", "available_balance", "locked_amount", "remaining_available", "sender_balance",
    "transferred_amount", "amount", "daily_limit", "collected_amount", "outstanding_amount", "per_person",
    "redeemed_amount", "initiator_share", "released_amount",
)
private val PRIMARY_AMOUNT_FIELDS = listOf(
    "transferred_amount", "redeemed_amount", "released_amount", "total_balance", "locked_amount", "amount",
)
/** The core stores UTC; a receipt must show the device zone or it contradicts the reply text. */
private val TIMESTAMP_FIELDS = setOf("scheduled_at", "created_at", "collected_at")
private fun formatValue(key: String, value: Any?): String = when {
    value is Number && key in MONEY_FIELDS && value.toDouble().isFinite() -> money(value.toDouble())
    value is Boolean -> if (value) "是" else "否"
    key == "status" && value is String -> displayStatus(value)
    key == "operation" && value is String -> executionTitle(value).takeUnless { it == "处理结果" } ?: value
    key == "risk_level" && value is String -> when (value) { "GREEN" -> "低风险"; "YELLOW" -> "需确认"; "RED" -> "需身份验证"; else -> value }
    key == "currency" && value == "CNY" -> "人民币"
    key in TIMESTAMP_FIELDS && value is String -> formatLocalTimestamp(value) ?: value
    value is Number && value.toDouble().isFinite() && value.toDouble() % 1.0 == 0.0 -> String.format(Locale.CHINA, "%,.0f", value.toDouble())
    else -> value?.toString() ?: "—"
}
private fun displayStatus(status: String): String = when (status) {
    "SUCCEEDED", "SUCCESS", "COMPLETED" -> "已完成"
    "RUNNING" -> "处理中"
    "PENDING" -> "待处理"
    "WAITING_USER" -> "等待您的确认"
    "FAILED", "FAILURE", "ERROR" -> "处理失败"
    "BLOCKED", "DENIED", "REJECTED" -> "已拦截"
    "EXPIRED" -> "已过期"
    "CANCELLED" -> "已取消"
    "PENDING_REVIEW" -> "等待人工审核"
    "CARD_PROTECTED" -> "卡片已保护"
    "FROZEN" -> "已冻结"
    "ACTIVE" -> "正常"
    "LOCKED" -> "已锁定"
    "LOST" -> "已挂失"
    "ISSUED" -> "已发卡"
    "LIMIT_UPDATED" -> "限额已更新"
    "PURCHASED" -> "已申购"
    "REDEEMED" -> "已赎回"
    "SCHEDULED" -> "已预约"
    "COLLECTED" -> "已收齐"
    "SETTLED" -> "已结算"
    "PARTIALLY_COLLECTED", "PARTIAL" -> "部分已收款"
    "PENDING_COLLECTION" -> "待收款"
    "CREATED" -> "已创建"
    "PASSWORD_CHANGED" -> "密码已修改"
    else -> status
}
/** Unknown shapes remain absent; numeric facts must be finite. */
private fun Map<String, Any?>.child(key: String): Map<String, Any?>? {
    val value = this[key] as? Map<*, *> ?: return null
    return value.entries.associate { it.key.toString() to it.value }
}
private fun Map<String, Any?>.children(key: String): List<Map<String, Any?>> {
    val value = this[key] as? List<*> ?: return emptyList()
    return value.mapNotNull { item -> (item as? Map<*, *>)?.entries?.associate { it.key.toString() to it.value } }
}
private fun Map<String, Any?>.text(key: String): String? = this[key]?.toString()
private fun Map<String, Any?>.number(key: String): Double? = (this[key] as? Number)?.toDouble()?.takeIf { it.isFinite() }
private fun Map<String, Any?>.money(key: String): String? = number(key)?.let { money(it) }
private fun money(value: Double): String = "¥" + String.format(Locale.CHINA, "%,.2f", value)
