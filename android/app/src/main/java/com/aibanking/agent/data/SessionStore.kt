package com.aibanking.agent.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Encrypted storage for the session credential.
 *
 * A session token is a bearer credential: whoever holds it acts as the user
 * until it expires.  Writing it into plaintext preferences would leak it to
 * anything able to read the app's data directory, so each value is sealed with
 * an AES-GCM key that lives in the Android Keystore and never leaves it.
 */
class SessionStore(context: Context) {

    private val preferences = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun saveSession(token: String, principal: String, conversationId: String) {
        preferences.edit()
            .putString(KEY_TOKEN, seal(token))
            .putString(KEY_PRINCIPAL, seal(principal))
            .putString(KEY_CONVERSATION, seal(conversationId))
            .apply()
    }

    fun sessionToken(): String? = open(KEY_TOKEN)

    fun principal(): String? = open(KEY_PRINCIPAL)

    fun conversationId(): String? = open(KEY_CONVERSATION)

    /** Persist the enrolled device id.
     *
     *  Not a credential, but re-enrolling on every launch would be pointless
     *  churn: each enrollment is an audited event on the bank side.
     */
    fun saveDeviceId(deviceId: String) {
        preferences.edit().putString(KEY_DEVICE, seal(deviceId)).apply()
    }

    fun deviceId(): String? = open(KEY_DEVICE)

    /** Persist the (already JSON-serialized) chat transcript, sealed like a credential. */
    fun saveMessages(json: String) {
        preferences.edit().putString(KEY_MESSAGES, seal(json)).apply()
    }

    fun messages(): String? = open(KEY_MESSAGES)

    /**
     * Drop the credential material.
     *
     * The device id survives on purpose: the device stays enrolled with the bank
     * across a logout, and the Keystore key backing it is not a session
     * credential.  Only an explicit unenroll (or a lost-device report) should
     * remove it.  The chat transcript goes with the session: it belongs to the
     * authenticated user, not to the phone.
     */
    fun clear() {
        preferences.edit()
            .remove(KEY_TOKEN)
            .remove(KEY_PRINCIPAL)
            .remove(KEY_CONVERSATION)
            .remove(KEY_MESSAGES)
            .apply()
    }

    private fun open(key: String): String? {
        val stored = preferences.getString(key, null) ?: return null
        return runCatching { unseal(stored) }.getOrNull()
    }

    private fun seal(plainText: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, secretKey()) }
        val sealed = cipher.doFinal(plainText.toByteArray(Charsets.UTF_8))
        // GCM generates a fresh IV per encryption; it is not secret, so storing
        // it alongside the ciphertext is standard practice.
        return Base64.encodeToString(cipher.iv + sealed, Base64.NO_WRAP)
    }

    private fun unseal(encoded: String): String {
        val raw = Base64.decode(encoded, Base64.NO_WRAP)
        require(raw.size > IV_BYTES) { "stored value is truncated" }
        val cipher = Cipher.getInstance(TRANSFORMATION).apply {
            init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(TAG_BITS, raw, 0, IV_BYTES))
        }
        return String(cipher.doFinal(raw, IV_BYTES, raw.size - IV_BYTES), Charsets.UTF_8)
    }

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE).apply {
            init(
                KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build(),
            )
        }
        return generator.generateKey()
    }

    private companion object {
        const val PREFS_NAME = "bank_agent_session"
        const val KEY_ALIAS = "bank_agent_session_key_v1"
        const val KEY_TOKEN = "session_token"
        const val KEY_PRINCIPAL = "principal"
        const val KEY_CONVERSATION = "conversation_id"
        const val KEY_DEVICE = "device_id"
        const val KEY_MESSAGES = "chat_messages"
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val IV_BYTES = 12
        private const val TAG_BITS = 128
    }
}
