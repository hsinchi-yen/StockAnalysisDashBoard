package com.microeconomicdashboard.app

import android.annotation.SuppressLint
import android.os.Bundle
import android.view.View
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat

class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private lateinit var progressBar: ProgressBar
    private lateinit var statusText: TextView
    private lateinit var retryButton: Button
    private var latestTopInset: Int = 0
    private var latestBottomInset: Int = 0

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Extend content behind system bars so CSS env(safe-area-inset-*) works correctly
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        progressBar = findViewById(R.id.progressBar)
        statusText = findViewById(R.id.statusText)
        retryButton = findViewById(R.id.retryButton)

        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.settings.builtInZoomControls = false   // disable pinch-zoom
        webView.settings.displayZoomControls = false
        webView.settings.useWideViewPort = true
        webView.settings.loadWithOverviewMode = false  // don't auto-scale to fit
        webView.settings.allowFileAccess = false

        ViewCompat.setOnApplyWindowInsetsListener(webView) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            latestTopInset = bars.top
            latestBottomInset = bars.bottom
            pushInsetsToWebCss()
            insets
        }

        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                progressBar.visibility = View.GONE
                pushInsetsToWebCss()
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?,
            ) {
                if (request?.isForMainFrame == true) {
                    showError(error?.description?.toString() ?: getString(R.string.page_load_failed))
                }
            }
        }

        retryButton.setOnClickListener {
            startBackendAndLoad()
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

        startBackendAndLoad()
    }

    private fun pushInsetsToWebCss() {
        if (!::webView.isInitialized) return
        val js = """
            (function() {
              document.documentElement.style.setProperty('--android-safe-top', '${latestTopInset}px');
              document.documentElement.style.setProperty('--android-safe-bottom', '${latestBottomInset}px');
            })();
        """.trimIndent()
        webView.post {
            webView.evaluateJavascript(js, null)
        }
    }

    private fun startBackendAndLoad() {
        statusText.text = getString(R.string.status_starting_backend)
        statusText.visibility = View.VISIBLE
        retryButton.visibility = View.GONE
        progressBar.visibility = View.VISIBLE
        BackendRuntime.start(applicationContext)
        BackendRuntime.awaitHealthy(
            onReady = {
                runOnUiThread {
                    statusText.visibility = View.GONE
                    retryButton.visibility = View.GONE
                    progressBar.visibility = View.VISIBLE
                    webView.loadUrl(BackendRuntime.baseUrl)
                }
            },
            onError = { message ->
                runOnUiThread {
                    showError(message)
                }
            },
        )
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }

    private fun showError(message: String) {
        progressBar.visibility = View.GONE
        statusText.visibility = View.VISIBLE
        statusText.text = getString(R.string.status_backend_failed, message)
        retryButton.visibility = View.VISIBLE
    }
}
