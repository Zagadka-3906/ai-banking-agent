package com.aibanking.agent

import android.content.pm.ApplicationInfo
import android.content.res.XmlResourceParser
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.SdkSuppress
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import org.junit.runner.RunWith
import org.xmlpull.v1.XmlPullParser

/** Checks the installed package's backup policy, not an OEM's backup implementation. */
@RunWith(AndroidJUnit4::class)
@BankingRegression
class BackupPolicyTest {
    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun installed_application_does_not_allow_backup() {
        assertEquals(
            "The installed application must keep allowBackup disabled",
            0,
            context.applicationInfo.flags and ApplicationInfo.FLAG_ALLOW_BACKUP,
        )
        assertFalse(
            "The installed manifest must also disable legacy full backup",
            readInstalledApplication { it.getAttributeBooleanValue(ANDROID_NAMESPACE, "fullBackupContent", true) },
        )
    }

    @Test
    @SdkSuppress(minSdkVersion = 31)
    fun installed_manifest_excludes_all_domains_from_cloud_backup_and_device_transfer() {
        val rulesId = readInstalledApplication {
            it.getAttributeResourceValue(ANDROID_NAMESPACE, "dataExtractionRules", 0)
        }
        assertNotEquals("The installed manifest must reference data extraction rules", 0, rulesId)

        val exclusions = linkedMapOf<String, MutableSet<String>>()
        val parser = context.resources.getXml(rulesId)
        try {
            var channel: String? = null
            while (parser.next() != XmlPullParser.END_DOCUMENT) {
                when (parser.eventType) {
                    XmlPullParser.START_TAG -> when (parser.name) {
                        "data-extraction-rules" -> assertEquals("Rules must be the XML root", 1, parser.depth)
                        "cloud-backup", "device-transfer" -> {
                            assertEquals("Backup channels must be direct children of the rules", 2, parser.depth)
                            val currentChannel = parser.name
                            assertTrue("Duplicate backup channel: $currentChannel", currentChannel !in exclusions)
                            exclusions[currentChannel] = linkedSetOf()
                            channel = currentChannel
                        }
                        "exclude" -> {
                            assertEquals("Exclusions must belong directly to a backup channel", 3, parser.depth)
                            val currentChannel = checkNotNull(channel) { "Exclusion has no backup channel" }
                            val domain = parser.getAttributeValue(null, "domain")
                            assertTrue("Unexpected excluded domain: $domain", domain in ALL_DOMAINS)
                            assertEquals("Every domain must be excluded in full", ".", parser.getAttributeValue(null, "path"))
                            assertTrue(
                                "Duplicate exclusion in $currentChannel: $domain",
                                exclusions.getValue(currentChannel).add(checkNotNull(domain)),
                            )
                        }
                        "include" -> fail("Banking data must not be included in any backup channel")
                        else -> fail("Unexpected backup policy element: ${parser.name}")
                    }
                    XmlPullParser.END_TAG -> if (parser.name == "cloud-backup" || parser.name == "device-transfer") {
                        channel = null
                    }
                }
            }
        } finally {
            parser.close()
        }
        assertEquals(
            "Both backup channels must have explicit policies",
            setOf("cloud-backup", "device-transfer"),
            exclusions.keys,
        )
        exclusions.forEach { (channel, domains) ->
            assertEquals("$channel must exclude every storage domain", ALL_DOMAINS, domains)
        }
    }

    private fun <T> readInstalledApplication(read: (XmlResourceParser) -> T): T {
        val parser = context.assets.openXmlResourceParser("AndroidManifest.xml")
        try {
            while (parser.next() != XmlPullParser.END_DOCUMENT) {
                if (parser.eventType == XmlPullParser.START_TAG && parser.name == "application") {
                    return read(parser)
                }
            }
        } finally {
            parser.close()
        }
        error("Installed manifest has no application element")
    }

    private companion object {
        const val ANDROID_NAMESPACE = "http://schemas.android.com/apk/res/android"
        val ALL_DOMAINS = setOf(
            "root", "file", "database", "sharedpref", "external",
            "device_root", "device_file", "device_database", "device_sharedpref",
        )
    }
}
