package org.allskyhub.allskyhub_app

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // lib/platform/wifi_binding.dart: keep traffic on the camera's setup Wi-Fi, which has
        // no internet, instead of falling back to mobile data.
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
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
    }
}
