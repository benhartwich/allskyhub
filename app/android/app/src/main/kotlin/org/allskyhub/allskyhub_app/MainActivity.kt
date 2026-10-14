package org.allskyhub.allskyhub_app

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiNetworkSpecifier
import android.os.Build
import android.os.PatternMatcher
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    // The request that holds the camera's setup Wi-Fi; released on "unbind".
    private var setupCallback: ConnectivityManager.NetworkCallback? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // lib/platform/sky_widget.dart: which camera the home-screen widget shows (roadmap N6).
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "org.allskyhub.app/widget")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "configure" -> {
                        SkyWidget.configure(
                            this,
                            call.argument<String>("hub") ?: "",
                            call.argument<String>("key") ?: "",
                            call.argument<String>("name") ?: "allskyhub",
                        )
                        result.success(pinWidget(call.argument<Boolean>("pin") == true))
                    }
                    else -> result.notImplemented()
                }
            }
        // lib/platform/wifi_binding.dart: keep traffic on the camera's setup Wi-Fi, which has
        // no internet, instead of falling back to mobile data; join it from the app.
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "org.allskyhub.app/wifi")
            .setMethodCallHandler { call, result ->
                val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
                when (call.method) {
                    "bindToWifi" -> {
                        @Suppress("DEPRECATION")
                        val wifi = cm.allNetworks.firstOrNull {
                            cm.getNetworkCapabilities(it)
                                ?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
                        }
                        result.success(wifi != null && cm.bindProcessToNetwork(wifi))
                    }
                    "unbind" -> {
                        cm.bindProcessToNetwork(null)
                        setupCallback?.let { cm.unregisterNetworkCallback(it) }
                        setupCallback = null
                        result.success(null)
                    }
                    "joinSetup" -> joinSetup(
                        cm,
                        call.argument<String>("ssid"),
                        call.argument<String>("prefix") ?: "allskyhub-",
                        call.argument<String>("key"),
                        result,
                    )
                    else -> result.notImplemented()
                }
            }
    }

    // Offers to put the widget on the home screen (Android 8+, if the launcher can);
    // "placed" when there already is one.
    private fun pinWidget(pin: Boolean): String {
        val manager = AppWidgetManager.getInstance(this)
        val provider = ComponentName(this, SkyWidget::class.java)
        if (manager.getAppWidgetIds(provider).isNotEmpty()) return "placed"
        if (!pin || Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return "manual"
        if (!manager.isRequestPinAppWidgetSupported) return "manual"
        return if (manager.requestPinAppWidget(provider, null, null)) "asked" else "manual"
    }

    // SPEC §7.1: the setup network allskyhub-XXXX, WPA2 with the setup key (or open for an
    // older camera). Android shows its own dialog to allow it.
    private fun joinSetup(
        cm: ConnectivityManager,
        ssid: String?,
        prefix: String,
        key: String?,
        result: MethodChannel.Result,
    ) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            result.success("unsupported")
            return
        }
        val spec = WifiNetworkSpecifier.Builder().apply {
            if (ssid != null) setSsid(ssid)
            else setSsidPattern(PatternMatcher(prefix, PatternMatcher.PATTERN_PREFIX))
            if (key != null) setWpa2Passphrase(key)
        }.build()
        val request = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .setNetworkSpecifier(spec)
            .build()
        setupCallback?.let { cm.unregisterNetworkCallback(it) }
        var answered = false
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                cm.bindProcessToNetwork(network)
                runOnUiThread {
                    if (!answered) {
                        answered = true
                        result.success("joined")
                    }
                }
            }

            override fun onUnavailable() {
                runOnUiThread {
                    if (!answered) {
                        answered = true
                        result.success("failed")
                    }
                }
            }
        }
        setupCallback = callback
        cm.requestNetwork(request, callback, 60_000)
    }
}
