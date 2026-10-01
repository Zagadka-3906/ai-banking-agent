package com.aibanking.agent.ui.chat

import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * 核心统一按 UTC 存 ISO-8601（`2026-10-02T02:00:00+00:00`），回执与任务列表若直接显示
 * 原始串，会和对话正文里的“北京时间 10:00”互相矛盾，因此时间戳字段渲染前都过这一层。
 */
internal fun formatLocalTimestamp(raw: String?): String? {
    val value = raw?.trim()
    if (value.isNullOrEmpty()) return raw
    // 解析失败必须原样返回：回执字段宁可显示核心给的原始串，也不能被格式化成空白。
    return runCatching {
        DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
            .withZone(ZoneId.systemDefault())
            .format(parseInstant(value))
    }.getOrDefault(raw)
}

/** 依次尝试带偏移量的时刻、`Z` 结尾的时刻，以及不带时区的本地时间（按设备时区解释）。 */
private fun parseInstant(value: String): Instant =
    runCatching { OffsetDateTime.parse(value).toInstant() }
        .getOrElse {
            runCatching { Instant.parse(value) }
                .getOrElse { LocalDateTime.parse(value).atZone(ZoneId.systemDefault()).toInstant() }
        }
