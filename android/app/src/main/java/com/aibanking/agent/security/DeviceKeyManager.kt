package com.aibanking.agent.security

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.os.Build
import android.util.Base64
import org.json.JSONObject
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * The per-device signing key behind the red authorization tier.
 *
 * The private key is generated inside the Android Keystore and never leaves it:
 * the app can only ask the Keystore for a signature over a server-issued
 * liveness challenge.  The matching public key is registered with the bank
 * after login (device enrollment), which is what lets the core verify the
 * biometric factor against *this device* rather than against a shared secret
 * shipped inside the APK - a shared secret can be extracted from the package
 * and would reduce a two-factor tier to OTP-only.
 *
 * The signed byte string is canonical and versioned so that the server can
 * reconstruct it exactly:
 *
 * ```
 * bank-agent-face-v1|<challenge_id>|<user_id>|<auth_token_jti>
 * ```
 */
class DeviceKeyManager(private val alias: String = DEFAULT_ALIAS) {

    /**
     * Load the device key pair, generating it on first use.
     *
     * StrongBox is requested when the device offers it (API 28+, dedicated
     * secure element) and the code falls back to the TEE-backed Keystore when
     * it is unavailable, so the app does not simply refuse to run on cheaper
     * hardware.
     */
    fun ensureKeyPair(): KeyPair {
        val keyStore = loadKeyStore()
        val existing = keyStore.getEntry(alias, null) as? KeyStore.PrivateKeyEntry
        if (existing != null) {
            return KeyPair(existing.certificate.publicKey, existing.privateKey)
        }
        return generateKeyPair(keyStore, strongBox = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P)
    }

    /** Base64(DER SubjectPublicKeyInfo) for device enrollment. */
    fun publicKeySpkiBase64(): String {
        val keyPair = ensureKeyPair()
        return Base64.encodeToString(keyPair.public.encoded, Base64.NO_WRAP)
    }

    /** Base64(DER ECDSA-SHA256) over the canonical challenge string. */
    fun signChallenge(challengeId: String, userId: String, authTokenJti: String): String {
        val keyPair = ensureKeyPair()
        val signature = Signature.getInstance(SIGNATURE_ALGORITHM).apply {
            initSign(keyPair.private as PrivateKey)
            update(canonicalPayload(challengeId, userId, authTokenJti).toByteArray(Charsets.UTF_8))
        }
        return Base64.encodeToString(signature.sign(), Base64.NO_WRAP)
    }

    /** Remove the device key, e.g. on logout or after a lost-device report. */
    fun deleteKey() {
        loadKeyStore().apply {
            if (containsAlias(alias)) deleteEntry(alias)
        }
    }

    private fun generateKeyPair(keyStore: KeyStore, strongBox: Boolean): KeyPair {
        return try {
            buildAndStore(keyStore, strongBox)
        } catch (error: Exception) {
            if (!strongBox) throw error
            // StrongBox is a hardware capability that can also be temporarily
            // unavailable (for example while another app holds it).  Fall back
            // rather than leaving the user unable to authorize anything.
            keyStore.deleteEntry(alias)
            buildAndStore(keyStore, strongBox = false)
        }
    }

    private fun buildAndStore(keyStore: KeyStore, strongBox: Boolean): KeyPair {
        val builder = KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
            .setAlgorithmParameterSpec(ECGenParameterSpec(CURVE))
            .setDigests(KeyProperties.DIGEST_SHA256)
            // A signature is usable only shortly after Android has authenticated
            // the device owner. The UI prompt alone is not an authorization proof.
            .setUserAuthenticationRequired(true)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            builder.setUserAuthenticationParameters(
                AUTH_WINDOW_SECONDS,
                KeyProperties.AUTH_BIOMETRIC_STRONG or KeyProperties.AUTH_DEVICE_CREDENTIAL,
            )
        } else {
            @Suppress("DEPRECATION")
            builder.setUserAuthenticationValidityDurationSeconds(AUTH_WINDOW_SECONDS)
        }
        if (strongBox && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            builder.setIsStrongBoxBacked(true)
        }
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, ANDROID_KEYSTORE).apply {
            initialize(builder.build())
        }
        val keyPair = generator.generateKeyPair()
        // ``generateKeyPair`` already persisted the entry; reading it back proves
        // the alias is usable before the caller relies on it.
        check(keyStore.containsAlias(alias)) { "device key was not persisted" }
        return keyPair
    }

    private fun loadKeyStore(): KeyStore =
        KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }

    companion object {
        const val DEFAULT_ALIAS = "bank_agent_device_key_v2_auth_bound"
        const val PAYLOAD_PREFIX = "bank-agent-face-v1"
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val CURVE = "secp256r1"
        private const val SIGNATURE_ALGORITHM = "SHA256withECDSA"
        // The challenge request occurs after the system prompt and may take a
        // network round trip; keep the window aligned with the Core's 60s TTL.
        private const val AUTH_WINDOW_SECONDS = 60

        /**
         * The exact bytes the device signs.  Kept here as the single source of
         * truth for the client side; the server reconstructs the same string
         * during verification.
         */
        fun canonicalPayload(challengeId: String, userId: String, authTokenJti: String): String =
            listOf(PAYLOAD_PREFIX, challengeId, userId, authTokenJti).joinToString("|")

        /**
         * Wrap a signature in the `v2:` envelope the core expects.
         *
         * The envelope names the enrolled device so the server can pick the
         * right public key; it deliberately carries no key material and no
         * nonce, so an intercepted envelope is useless without the private key
         * that produced the signature.
         */
        fun attestationEnvelope(deviceId: String, challengeId: String, signatureBase64: String): String {
            val envelope = JSONObject()
                .put("device_id", deviceId)
                .put("challenge_id", challengeId)
                .put("signature", signatureBase64)
            val encoded = Base64.encodeToString(envelope.toString().toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
            return ENVELOPE_PREFIX + encoded
        }

        const val ENVELOPE_PREFIX = "v2:"
    }
}
