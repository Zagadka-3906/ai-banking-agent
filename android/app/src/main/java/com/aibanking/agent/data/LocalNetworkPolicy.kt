package com.aibanking.agent.data

import java.net.URI
import java.util.Locale

/**
 * Decide from configured URLs only; never resolve a name or open a socket to
 * decide whether to ask for permission. Public names can resolve to private
 * addresses, so deployments using split DNS can opt in explicitly at build
 * time. Loopback traffic does not need automatic LAN access permission.
 */
internal fun requiresLocalNetworkPermission(
    sdkInt: Int,
    vararg endpoints: String,
    explicitlyRequired: Boolean = false,
): Boolean = sdkInt >= 37 && (explicitlyRequired || endpoints.any(::endpointUsesLocalNetwork))

internal fun endpointUsesLocalNetwork(endpoint: String): Boolean {
    val uri = runCatching { URI(endpoint.trim()) }.getOrNull() ?: return false
    if (uri.scheme?.lowercase(Locale.ROOT) !in setOf("http", "https")) return false
    val host = uri.host?.lowercase(Locale.ROOT)?.removeSurrounding("[", "]")
        ?.removeSuffix(".") ?: return false
    if (host == "localhost" || host.endsWith(".localhost")) return false
    if (':' in host) {
        val words = parseIpv6Literal(host.substringBefore('%')) ?: return false
        // IPv4-mapped IPv6 literals retain the IPv4 address's scope.
        if (words.take(5).all { it == 0 } && words[5] == 0xffff) {
            return isLocalIpv4(listOf(words[6] shr 8, words[6] and 0xff, words[7] shr 8, words[7] and 0xff))
        }
        return (words[0] and 0xfe00) == 0xfc00 || // unique local: fc00::/7
            (words[0] and 0xffc0) == 0xfe80 || // link local: fe80::/10
            (words[0] and 0xffc0) == 0xfec0 // deprecated site local: fec0::/10
    }
    parseIpv4Literal(host)?.let { return isLocalIpv4(it) }
    return host.endsWith(".local") || ('.' !in host && host.any { !it.isDigit() })
}

private fun isLocalIpv4(bytes: List<Int>): Boolean =
    bytes[0] == 10 ||
        (bytes[0] == 172 && bytes[1] in 16..31) ||
        (bytes[0] == 192 && bytes[1] == 168) ||
        (bytes[0] == 169 && bytes[1] == 254)

private fun parseIpv4Literal(value: String): List<Int>? {
    val parts = value.split('.')
    if (parts.size != 4) return null
    return parts.map { part ->
        if (part.isEmpty() || part.length > 3 || part.any { it !in '0'..'9' }) return null
        part.toInt().takeIf { it in 0..255 } ?: return null
    }
}

/** Parse numeric IPv6 locally, including compressed and IPv4-mapped forms. */
private fun parseIpv6Literal(value: String): List<Int>? {
    val expandedV4 = if ('.' in value) {
        val bytes = parseIpv4Literal(value.substringAfterLast(':')) ?: return null
        value.substringBeforeLast(':') + ":" + ((bytes[0] shl 8) or bytes[1]).toString(16) +
            ":" + ((bytes[2] shl 8) or bytes[3]).toString(16)
    } else value
    val halves = expandedV4.split("::")
    if (halves.size > 2) return null
    fun words(part: String): List<Int>? {
        if (part.isEmpty()) return emptyList()
        return part.split(':').map {
            if (it.isEmpty() || it.length > 4 || it.any { char -> char !in '0'..'9' && char !in 'a'..'f' }) return null
            it.toInt(16)
        }
    }
    val left = words(halves[0]) ?: return null
    if (halves.size == 1) return left.takeIf { it.size == 8 }
    val right = words(halves[1]) ?: return null
    val omitted = 8 - left.size - right.size
    if (omitted < 1) return null
    return left + List(omitted) { 0 } + right
}
