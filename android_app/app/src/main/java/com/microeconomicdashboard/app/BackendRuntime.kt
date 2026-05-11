package com.microeconomicdashboard.app

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

object BackendRuntime {
    private const val host = "127.0.0.1"
    private const val port = 8000

    val baseUrl: String = "http://$host:$port/"

    private val serverExecutor = Executors.newSingleThreadExecutor()
    private val probeExecutor = Executors.newSingleThreadExecutor()
    private val started = AtomicBoolean(false)

    @Volatile
    private var startupFailure: Throwable? = null

    fun start(context: Context) {
        startupFailure = null
        if (!started.compareAndSet(false, true)) {
            return
        }

        serverExecutor.execute {
            try {
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(context.applicationContext))
                }

                val cacheDir = File(context.cacheDir, "microeconomic-cache").apply {
                    mkdirs()
                }
                val staticDir = File(context.filesDir, "static-runtime")
                AssetCopier.copyDirectory(context, "static", staticDir)

                Python.getInstance()
                    .getModule("android_launcher")
                    .callAttr(
                        "start_server",
                        host,
                        port,
                        cacheDir.absolutePath,
                        staticDir.absolutePath,
                    )
            } catch (throwable: Throwable) {
                startupFailure = throwable
                started.set(false)
            }
        }
    }

    fun awaitHealthy(onReady: () -> Unit, onError: (String) -> Unit) {
        probeExecutor.execute {
            repeat(60) {
                val failure = startupFailure
                if (failure != null) {
                    onError(failure.message ?: failure.toString())
                    return@execute
                }

                if (isHealthy()) {
                    onReady()
                    return@execute
                }

                Thread.sleep(1000)
            }

            onError("Local dashboard service did not become ready within 60 seconds.")
        }
    }

    private fun isHealthy(): Boolean {
        return try {
            val connection = URL("${baseUrl}api/health").openConnection() as HttpURLConnection
            connection.requestMethod = "GET"
            connection.connectTimeout = 1500
            connection.readTimeout = 1500
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            connection.connect()
            val healthy = connection.responseCode in 200..299
            connection.disconnect()
            healthy
        } catch (_: Exception) {
            false
        }
    }
}
