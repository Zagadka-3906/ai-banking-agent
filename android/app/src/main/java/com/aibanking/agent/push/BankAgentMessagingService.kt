package com.aibanking.agent.push

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import androidx.core.app.NotificationCompat
import com.aibanking.agent.MainActivity
import com.aibanking.agent.data.DeviceRepository
import com.aibanking.agent.data.SessionStore
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.runBlocking

/**
 * Receives the device's registration token, and the notifications themselves.
 *
 * Both callbacks only ever run when a Firebase project is configured; without
 * one there is nothing to deliver and this service is never invoked.  See
 * [PushNotifications] for how that absence is detected elsewhere.
 *
 * The server sends *data* messages so that [onMessageReceived] is the thing that
 * runs and the app controls the wording and the deep link, rather than the
 * system rendering a notification the app never sees.
 */
class BankAgentMessagingService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        // Registration is bound to the signed-in principal and to this device, so
        // it needs a session.  A token that arrives before login is dropped and
        // picked up again after login (ChatViewModel reads the current token once
        // the device is enrolled), so nothing is lost.
        val store = SessionStore(this)
        val principal = store.principal() ?: return
        val session = store.sessionToken() ?: return
        val deviceId = store.deviceId() ?: return

        // Called on a Firebase worker thread, so a short blocking call is fine
        // and avoids racing process death.
        runBlocking {
            runCatching { DeviceRepository.create().registerPushToken(deviceId, token, principal, session) }
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val title = message.notification?.title ?: message.data["title"] ?: DEFAULT_TITLE
        val body = message.notification?.body ?: message.data["body"] ?: DEFAULT_BODY
        post(title, body, message.data["task_id"])
    }

    private fun post(title: String, body: String, taskId: String?) {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        // minSdk is 26, so the channel API is always available.
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_DEFAULT),
        )

        val intent = Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val pending = PendingIntent.getActivity(
            this,
            0,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setContentIntent(pending)
            .build()

        // Keyed by task so a re-delivered notification replaces its predecessor
        // instead of stacking duplicates of the same pending confirmation.
        manager.notify(taskId?.hashCode() ?: NOTIFICATION_ID, notification)
    }

    private companion object {
        const val CHANNEL_ID = "bank_agent_tasks"
        const val CHANNEL_NAME = "任务提醒"
        const val NOTIFICATION_ID = 1001
        const val DEFAULT_TITLE = "任务提醒"
        const val DEFAULT_BODY = "有一项任务需要您的确认。"
    }
}
