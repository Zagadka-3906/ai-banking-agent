package com.aibanking.agent

import com.aibanking.agent.security.DeviceKeyManager
import org.junit.Assert.*
import org.junit.Test

class DeviceKeyAliasTest {
    @Test fun accounts_keep_distinct_stable_aliases_without_plaintext_identifiers() {
        val alice = DeviceKeyManager.aliasForPrincipal("user_zhang")
        val bob = DeviceKeyManager.aliasForPrincipal("user_li")
        assertNotEquals(alice, bob)
        assertNotEquals(DeviceKeyManager.DEFAULT_ALIAS, alice)
        assertEquals(alice, DeviceKeyManager.aliasForPrincipal("user_zhang"))
        assertFalse(alice.contains("user_zhang"))
        assertTrue(alice.matches(Regex("bank_agent_device_key_v3_[0-9a-f]{64}")))
    }

    @Test fun aliases_do_not_collapse_distinct_authenticated_subjects() {
        assertNotEquals(DeviceKeyManager.aliasForPrincipal("user_li"), DeviceKeyManager.aliasForPrincipal("USER_LI"))
        assertNotEquals(DeviceKeyManager.aliasForPrincipal("甲"), DeviceKeyManager.aliasForPrincipal("乙"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun anonymous_principals_cannot_get_a_signing_identity() {
        DeviceKeyManager.aliasForPrincipal(" ")
    }
}
