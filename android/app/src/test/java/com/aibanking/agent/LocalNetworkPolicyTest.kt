package com.aibanking.agent

import com.aibanking.agent.data.endpointUsesLocalNetwork
import com.aibanking.agent.data.requiresLocalNetworkPermission
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalNetworkPolicyTest {
    @Test fun internal_dns_opt_in_still_requires_android_17() {
        assertTrue(requiresLocalNetworkPermission(37, "https://bank.example/", explicitlyRequired = true))
        assertFalse(requiresLocalNetworkPermission(36, "https://bank.example/", explicitlyRequired = true))
        assertFalse(requiresLocalNetworkPermission(37, "https://bank.example/", explicitlyRequired = false))
        assertTrue(requiresLocalNetworkPermission(37, "https://core.local/", explicitlyRequired = false))
    }

    @Test fun permission_is_required_only_on_api_37_and_for_either_local_endpoint() {
        for (sdk in listOf(26, 35, 36)) {
            assertFalse(requiresLocalNetworkPermission(sdk, "http://10.0.2.2:8000/", "https://core.local/"))
        }
        assertTrue(requiresLocalNetworkPermission(37, "https://public.example/", "http://10.0.2.2:8100/"))
        assertTrue(requiresLocalNetworkPermission(38, "https://core.local/", "https://public.example/"))
        assertFalse(requiresLocalNetworkPermission(37, "http://127.0.0.1:18800/", "http://localhost:18810/"))
        assertFalse(requiresLocalNetworkPermission(37))
    }

    @Test fun ipv4_private_and_link_local_ranges_include_their_exact_boundaries() {
        val local = listOf("10.0.0.0", "10.255.255.255", "172.16.0.0", "172.31.255.255",
            "192.168.0.0", "192.168.255.255", "169.254.0.0", "169.254.255.255")
        local.forEach { assertTrue(it, endpointUsesLocalNetwork("http://$it:8000/")) }
        val nonLocal = listOf("9.255.255.255", "11.0.0.0", "172.15.255.255", "172.32.0.0",
            "192.167.255.255", "192.169.0.0", "169.253.255.255", "169.255.0.0", "8.8.8.8")
        nonLocal.forEach { assertFalse(it, endpointUsesLocalNetwork("https://$it/")) }
    }

    @Test fun localhost_and_all_loopback_forms_do_not_request_lan_access() {
        listOf("localhost", "LOCALHOST.", "api.localhost", "127.0.0.1", "127.255.255.254",
            "[::1]", "[0:0:0:0:0:0:0:1]", "[::ffff:127.0.0.1]", "[::ffff:7f00:1]").forEach {
            assertFalse(it, endpointUsesLocalNetwork("http://$it:18800/"))
        }
    }

    @Test fun ipv6_private_link_and_site_local_ranges_are_parsed_without_dns() {
        listOf("fc00::1", "fdff:ffff::1", "fe80::1", "febf:ffff::1", "fec0::1", "feff::1",
            "FE80::1234%25eth0", "fc00:0:0:0:0:0:0:1").forEach {
            assertTrue(it, endpointUsesLocalNetwork("https://[$it]:8000/"))
        }
        listOf("fbff::1", "fe00::1", "fe7f::1", "2001:4860:4860::8888", "::", "::1").forEach {
            assertFalse(it, endpointUsesLocalNetwork("https://[$it]/"))
        }
    }

    @Test fun mapped_ipv6_uses_the_embedded_ipv4_scope() {
        listOf("::ffff:10.0.2.2", "::ffff:172.16.0.1", "::ffff:192.168.1.2", "::ffff:a9fe:1",
            "0:0:0:0:0:ffff:c0a8:102").forEach {
            assertTrue(it, endpointUsesLocalNetwork("http://[$it]:8000/"))
        }
        listOf("::ffff:8.8.8.8", "::ffff:127.0.0.1", "::ffff:172.32.0.1").forEach {
            assertFalse(it, endpointUsesLocalNetwork("http://[$it]/"))
        }
    }

    @Test fun only_explicit_local_hostnames_are_classified_as_local() {
        listOf("core", "bank-gateway", "CORE.LOCAL", "core.local.").forEach {
            assertTrue(it, endpointUsesLocalNetwork("https://$it/"))
        }
        listOf("bank.example", "core.local.example", "10.0.2.2.example", "mylocal.com").forEach {
            assertFalse(it, endpointUsesLocalNetwork("https://$it/"))
        }
        assertFalse(endpointUsesLocalNetwork("https://public.example/?next=http://10.0.2.2/"))
        assertFalse(endpointUsesLocalNetwork("https://core.local@public.example/"))
    }

    @Test fun malformed_and_non_http_endpoints_do_not_trigger_a_permission_prompt() {
        listOf("", "not a URL", "/core.local", "file://core.local/path", "http://", "http://[fe80:::1]/",
            "http://[fc00::1::2]/", "http://[fe80::zz]/", "http://256.1.1.1/", "http://10.0.2.999/").forEach {
            assertFalse(it, endpointUsesLocalNetwork(it))
        }
    }
}
