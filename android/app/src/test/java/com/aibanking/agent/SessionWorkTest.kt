package com.aibanking.agent

import com.aibanking.agent.ui.chat.SessionWork
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import org.junit.After
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread
import kotlin.coroutines.CoroutineContext

class SessionWorkTest {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
    private val writes = QueuedDispatcher()
    private val sessions = SessionWork(scope, writes)

    @After fun close() { scope.cancel() }

    @Test fun delayedSuccessCannotPublishAfterLogoutAndAnotherLogin() = runBlocking {
        val response = CompletableDeferred<String>()
        var visible = "A"
        val request = sessions.launch {
            val result = await { withContext(NonCancellable) { response.await() } }
            visible = result
        }
        sessions.replace { visible = "logged out" }
        sessions.replace { visible = "B" }
        response.complete("A secret balance")
        request.join()
        assertEquals("B", visible)
        assertTrue(request.isCancelled)
    }

    @Test fun previousLoginUnauthorizedErrorCannotExpireNewLogin() = runBlocking {
        val response = CompletableDeferred<Unit>()
        var authenticated = true
        var error: String? = null
        val request = sessions.launch {
            try {
                await { withContext(NonCancellable) { response.await(); error("401 expired") } }
            } catch (failure: CancellationException) {
                throw failure
            } catch (failure: Exception) {
                authenticated = false
                error = failure.message
            }
        }
        sessions.replace()
        response.complete(Unit)
        request.join()
        assertTrue(authenticated)
        assertNull(error)
    }

    @Test fun loggingIntoSameAccountAgainInvalidatesOldCallbacks() {
        var principal = "alice"
        val old = sessions.capture()
        sessions.replace { principal = "alice" }
        assertFalse(old.commit { principal = "old callback" })
        assertEquals("alice", principal)
    }

    @Test fun replacingSessionCancelsItsRunningRequests() = runBlocking {
        val request = sessions.launch { await { awaitCancellation() } }
        sessions.replace()
        request.join()
        assertTrue(request.isCancelled)
    }

    @Test fun laterLoginWinsWhenEarlierLoginTransportIgnoresCancellation() = runBlocking {
        val firstLogin = CompletableDeferred<String>()
        var credential: String? = null
        val first = sessions.launch {
            credential = await { withContext(NonCancellable) { firstLogin.await() } }
        }
        sessions.replace()
        sessions.launch { credential = await { "second login" } }.join()
        firstLogin.complete("first login")
        first.join()
        assertEquals("second login", credential)
    }

    @Test fun renewalKeepsExistingRequestAndCallbackOwnership() = runBlocking {
        var token = "original"
        val owner = sessions.capture()
        val response = CompletableDeferred<Unit>()
        var nextRequestToken: String? = null
        val request = sessions.launch {
            await { response.await() }
            commit { nextRequestToken = token }
        }
        token = "renewed"
        response.complete(Unit)
        request.join()
        assertEquals("renewed", nextRequestToken)
        assertTrue(owner.commit {})
        assertFalse(request.isCancelled)
    }

    @Test fun replacedChallengeCannotStartSigningOrApprovalRequest() = runBlocking {
        val challenge = CompletableDeferred<String>()
        var signed = false
        var submitted = false
        val request = sessions.launch {
            await { withContext(NonCancellable) { challenge.await() } }
            signed = true
            await { submitted = true }
        }
        sessions.replace()
        challenge.complete("old challenge")
        request.join()
        assertFalse(signed)
        assertFalse(submitted)
    }

    @Test fun staleStreamCallbackFromIoThreadCannotChangeNewConversation() {
        val old = sessions.capture()
        val visible = AtomicReference("B conversation")
        sessions.replace()
        val callback = thread { old.commit { visible.set("A progress") } }
        callback.join(2_000)
        assertFalse(callback.isAlive)
        assertEquals("B conversation", visible.get())
    }

    @Test fun staleReconciliationFinallyCannotSaveNewUsersTranscript() = runBlocking {
        val response = CompletableDeferred<Unit>()
        var finallyRan = false
        var saved: String? = null
        val request = sessions.launch {
            try {
                await { withContext(NonCancellable) { response.await() } }
            } finally {
                finallyRan = true
                commit { sessions.persist("messages") { saved = "A transcript" } }
            }
        }
        sessions.replace()
        sessions.persist("messages") { saved = "B transcript" }
        response.complete(Unit)
        request.join()
        writes.runAll()
        assertTrue(finallyRan)
        assertEquals("B transcript", saved)
    }

    @Test fun queuedPersistenceCannotRepopulateStorageAfterLogout() {
        var saved: String? = null
        sessions.persist("messages") { saved = "A secret" }
        sessions.replace { saved = null }
        writes.runAll()
        assertNull(saved)
    }

    @Test fun queuedOlderTranscriptCannotOverwriteLatestWithinSameLogin() {
        var saved: String? = null
        sessions.persist("messages") { saved = "first answer" }
        sessions.persist("messages") { saved = "second answer" }
        writes.runLast()
        writes.runAll()
        assertEquals("second answer", saved)
    }

    @Test fun transcriptAndQuizSnapshotsHaveIndependentWriteOrdering() {
        val saved = mutableMapOf<String, String>()
        sessions.persist("messages") { saved["messages"] = "answer" }
        sessions.persist("quiz") { saved["quiz"] = "five answers" }
        writes.runAll()
        assertEquals(mapOf("messages" to "answer", "quiz" to "five answers"), saved)
    }

    @Test fun failedEncryptedCacheWriteDoesNotCrashOrInvalidateSession() {
        val failed = sessions.persist("messages") { throw IllegalStateException("Keystore unavailable") }
        var saved = false
        sessions.persist("quiz") { saved = true }
        writes.runAll()
        assertTrue(failed.isCompleted)
        assertFalse(failed.isCancelled)
        assertTrue(saved)
        assertTrue(sessions.capture().commit {})
    }

    @Test fun logoutClearIsOrderedAfterAnAlreadyStartedEncryptedWrite() {
        val old = sessions.capture()
        val writing = CountDownLatch(1)
        val finishWrite = CountDownLatch(1)
        val replacing = CountDownLatch(1)
        val stored = AtomicReference<String?>(null)
        val failure = AtomicReference<Throwable?>(null)
        val writer = thread {
            try {
                old.commit {
                    writing.countDown()
                    check(finishWrite.await(2, TimeUnit.SECONDS))
                    stored.set("A sealed credential")
                }
            } catch (problem: Throwable) { failure.set(problem) }
        }
        assertTrue(writing.await(2, TimeUnit.SECONDS))
        val logout = thread {
            replacing.countDown()
            sessions.replace { stored.set(null) }
            sessions.capture().commit { stored.set("B sealed credential") }
        }
        assertTrue(replacing.await(2, TimeUnit.SECONDS))
        finishWrite.countDown()
        writer.join(2_000)
        logout.join(2_000)
        assertNull(failure.get())
        assertFalse(writer.isAlive)
        assertFalse(logout.isAlive)
        assertEquals("B sealed credential", stored.get())
        assertFalse(old.commit { stored.set("late A write") })
    }

    @Test fun externalCallerCannotReceiveSensitiveResultForReplacedSession() = runBlocking {
        val owner = sessions.capture()
        val response = CompletableDeferred<String>()
        var revealed: String? = null
        // Like a card-detail composable, the caller is outside the session job.
        val caller = scope.launch {
            revealed = owner.await { response.await() }
        }
        sessions.replace()
        response.complete("A full card number")
        caller.join()
        assertNull(revealed)
        assertTrue(caller.isCancelled)
    }

    @Test fun cancelingOneRequestDoesNotInvalidateOtherWorkInSameLogin() = runBlocking {
        val response = CompletableDeferred<Unit>()
        var published = false
        val request = sessions.launch {
            await { withContext(NonCancellable) { response.await() } }
            published = true
        }
        request.cancel()
        response.complete(Unit)
        request.join()
        assertFalse(published)
        assertTrue(sessions.capture().commit {})
    }

    @Test fun currentSessionFailureStillReachesErrorHandler() = runBlocking {
        var message: String? = null
        sessions.launch {
            try {
                await { throw IllegalStateException("network unavailable") }
            } catch (failure: IllegalStateException) {
                message = failure.message
            }
        }.join()
        assertEquals("network unavailable", message)
    }

    private class QueuedDispatcher : CoroutineDispatcher() {
        private val queue = ArrayDeque<Runnable>()
        override fun dispatch(context: CoroutineContext, block: Runnable) { queue.addLast(block) }
        fun runLast() { queue.removeLast().run() }
        fun runAll() { while (queue.isNotEmpty()) queue.removeFirst().run() }
    }
}
