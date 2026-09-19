package com.aibanking.agent.push

import android.content.Context
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import java.util.concurrent.TimeUnit

/**
 * Whether this build can actually receive push notifications.
 *
 * The google-services plugin is applied only when `android/app/google-services.json`
 * exists (see app/build.gradle.kts): it refuses to configure the build at all
 * without that file, so a checkout with no Firebase project must still compile.
 * `FirebaseApp` initialises from the resources that file generates, so its
 * absence shows up as "no app registered" rather than as a crash, and every call
 * site asks here first.
 *
 * The consequence, stated plainly: **without a Firebase project nothing is
 * delivered.**  The device registers no token, the server has nothing to send
 * to, and `/api/v1/tasks/run-due` records `NOT_SENT` with the reason.  With the
 * file present the app fetches a real FCM token on login and registers it, and
 * the server side reports `push.configured` in `/api/v1/security/health`.
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
            // Some vendor builds can leave Play Services token acquisition
            // pending indefinitely. Push is optional, so it must have a hard
            // deadline and must never hold the signed-in UI hostage.
            com.google.android.gms.tasks.Tasks.await(task, 10, TimeUnit.SECONDS)
        }.getOrNull()?.takeIf { it.isNotBlank() }
    }
}
