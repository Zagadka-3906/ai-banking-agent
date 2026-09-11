package com.aibanking.agent.push

import android.content.Context
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging

/**
 * Whether this build can actually receive push notifications.
 *
 * The google-services plugin is deliberately not applied (it refuses to
 * configure the build without a real `google-services.json`), so a checkout with
 * no Firebase project must still run - it simply has no push.  `FirebaseApp`
 * initialises from generated resources, so the absence shows up as "no app
 * registered" rather than as a crash, and every call site asks here first.
 *
 * The consequence, stated plainly: **without a Firebase project nothing is
 * delivered.**  The device registers no token, the server has nothing to send
 * to, and `/api/v1/tasks/run-due` records `NOT_SENT` with the reason.  Adding
 * `google-services.json` and applying the plugin is what turns delivery on.
 */
object PushNotifications {

    /** Whether a Firebase app was configured at startup. */
    fun isConfigured(context: Context): Boolean =
        runCatching { FirebaseApp.getApps(context).isNotEmpty() }.getOrDefault(false)

    /**
     * The current FCM registration token, or null when push is unconfigured or
     * the token cannot be fetched.
     *
     * Blocking: callers are already off the main thread (a service callback or a
     * coroutine on Dispatchers.IO).
     */
    fun currentToken(context: Context): String? {
        if (!isConfigured(context)) return null
        return runCatching {
            val task = FirebaseMessaging.getInstance().token
            com.google.android.gms.tasks.Tasks.await(task)
        }.getOrNull()?.takeIf { it.isNotBlank() }
    }
}
