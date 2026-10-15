package com.aibanking.agent.ui.chat

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Owns work for one login attempt/session, independently of its rotating token.
 * Cancellation saves work; the generation check also rejects transports and
 * callbacks that finish despite cancellation. The same lock orders persistence
 * against a logout/new login, so a late write cannot resurrect old credentials.
 */
internal class SessionWork(
    private val scope: CoroutineScope,
    private val persistenceDispatcher: CoroutineDispatcher = Dispatchers.IO,
) {
    private val lock = Any()
    private var generation = 0L
    private var job = SupervisorJob(scope.coroutineContext[Job])
    private val writeVersions = mutableMapOf<String, Long>()

    fun replace(clear: () -> Unit = {}) = synchronized(lock) {
        generation += 1
        val previous = job
        job = SupervisorJob(scope.coroutineContext[Job])
        writeVersions.clear()
        previous.cancel()
        clear()
    }

    fun capture(): Lease = synchronized(lock) { Lease(generation, job) }

    fun launch(block: suspend Lease.() -> Unit): Job {
        val lease = capture()
        // The session supervisor is already a child of the owner's Job. Launch
        // from that owned scope instead of replacing a builder's parent Job.
        return CoroutineScope(scope.coroutineContext + lease.job).launch {
            lease.check()
            lease.block()
        }
    }

    /** Later snapshots supersede queued older snapshots of the same value. */
    fun persist(key: String, write: () -> Unit): Job {
        val (lease, version) = synchronized(lock) {
            capture() to (writeVersions[key] ?: 0L).plus(1).also { writeVersions[key] = it }
        }
        return CoroutineScope(scope.coroutineContext + lease.job).launch(persistenceDispatcher) {
            try {
                lease.commit { if (writeVersions[key] == version) write() }
            } catch (failure: CancellationException) {
                throw failure
            } catch (_: Exception) {
                // Transcript/quiz caches are best effort. Keystore failure must
                // not turn a completed banking operation into an app crash.
            }
        }
    }

    inner class Lease internal constructor(private val owner: Long, internal val job: Job) {
        val isActive: Boolean get() = job.isActive

        fun check() = synchronized(lock) {
            if (owner != generation || !job.isActive) throw CancellationException("Login session replaced")
        }

        /** For callbacks and synchronous side effects, including encrypted writes. */
        fun commit(block: () -> Unit): Boolean = synchronized(lock) {
            if (owner != generation || !job.isActive) false else {
                block()
                true
            }
        }

        suspend fun <T> await(block: suspend () -> T): T {
            currentCoroutineContext().ensureActive()
            check()
            try {
                return block()
            } finally {
                // Also suppress late errors (especially a previous user's 401).
                currentCoroutineContext().ensureActive()
                check()
            }
        }

        suspend fun <T> io(block: suspend () -> T): T = await {
            withContext(Dispatchers.IO) {
                check()
                block()
            }
        }
    }
}
