package com.aibanking.agent.security

import android.os.Build
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity

/**
 * The local biometric gate for the red authorization tier.
 *
 * This prompt proves *presence*: the person holding the unlocked device is the
 * one approving.  It is deliberately not the security boundary on its own -
 * the device then signs the server-issued liveness challenge with its Keystore
 * key, and the core verifies that signature before it will settle anything.  A
 * rooted device that fakes the prompt still cannot forge the Keystore signature.
 */
object BiometricGate {

    private fun manager(activity: FragmentActivity): BiometricManager = BiometricManager.from(activity)

    /** Whether biometrics is enrolled on this device. */
    private fun hasBiometrics(activity: FragmentActivity): Boolean =
        manager(activity).canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_WEAK) ==
            BiometricManager.BIOMETRIC_SUCCESS

    /**
     * Whether a device credential (PIN / pattern / password) is set.
     *
     * Querying this separately rather than through a combined mask matters: a
     * mask that includes a `BIOMETRIC_*` flag reports "unsupported" unless
     * biometrics is *enrolled*, so a device secured only by a PIN - an emulator,
     * or a phone whose owner never enrolled a finger - was told the red tier was
     * unavailable even though the user could easily have been prompted.
     */
    private fun hasDeviceCredential(activity: FragmentActivity): Boolean =
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.R &&
            manager(activity).canAuthenticate(BiometricManager.Authenticators.DEVICE_CREDENTIAL) ==
            BiometricManager.BIOMETRIC_SUCCESS

    /** Whether this device can show a usable prompt at all. */
    fun canAuthenticate(activity: FragmentActivity): Boolean =
        hasBiometrics(activity) || hasDeviceCredential(activity)

    /**
     * The authenticator set to ask for, given what this device actually offers.
     *
     * Biometric-or-credential is only a legal combination from API 30; below
     * that the prompt must be biometric-only with an explicit negative button.
     */
    private fun allowedAuthenticators(activity: FragmentActivity): Int = when {
        hasBiometrics(activity) && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R ->
            BiometricManager.Authenticators.BIOMETRIC_WEAK or BiometricManager.Authenticators.DEVICE_CREDENTIAL

        hasBiometrics(activity) -> BiometricManager.Authenticators.BIOMETRIC_WEAK

        // Credential-only is API 30+; canAuthenticate() already ruled it out below.
        else -> BiometricManager.Authenticators.DEVICE_CREDENTIAL
    }

    fun prompt(
        activity: FragmentActivity,
        title: String,
        subtitle: String,
        onSuccess: () -> Unit,
        onFailure: (String) -> Unit,
    ) {
        val authenticators = allowedAuthenticators(activity)
        val executor = ContextCompat.getMainExecutor(activity)
        val prompt = BiometricPrompt(
            activity,
            executor,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    onSuccess()
                }

                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    onFailure(errString.toString())
                }

                // A non-matching finger is retryable inside the prompt, so it is
                // reported only when the prompt itself gives up.
                override fun onAuthenticationFailed() = Unit
            },
        )
        val builder = BiometricPrompt.PromptInfo.Builder()
            .setTitle(title)
            .setSubtitle(subtitle)
            .setAllowedAuthenticators(authenticators)
        if (authenticators == BiometricManager.Authenticators.BIOMETRIC_WEAK) {
            // Biometric-only prompts on API < 30 require a negative button;
            // setting one on a credential prompt throws.
            builder.setNegativeButtonText("取消")
        }
        prompt.authenticate(builder.build())
    }
}
