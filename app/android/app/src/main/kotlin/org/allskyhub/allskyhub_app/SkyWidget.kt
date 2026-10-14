package org.allskyhub.allskyhub_app

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.view.View
import android.widget.RemoteViews
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone
import kotlin.concurrent.thread

// Roadmap N6: the latest picture of one camera and a line about tonight on the home screen.
// lib/platform/sky_widget.dart stores the hub's address and a read-only widget key (never
// the app's own token, server api/widget.py); Android asks for an update every 30 minutes
// (res/xml/sky_widget_info.xml), and the app asks for one when it has set the camera.
class SkyWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        val pending = goAsync()
        thread {
            try {
                refresh(context, manager, ids)
            } finally {
                pending.finish()
            }
        }
    }

    override fun onDisabled(context: Context) {
        prefs(context).edit().clear().apply()
    }

    companion object {
        private const val PREFS = "allskyhub_widget"
        private const val TIMEOUT_MS = 8_000

        fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

        fun configure(context: Context, hub: String, key: String, name: String) {
            prefs(context).edit()
                .putString("hub", hub.trimEnd('/'))
                .putString("key", key)
                .putString("name", name)
                .apply()
            updateAll(context)
        }

        fun updateAll(context: Context) {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, SkyWidget::class.java))
            if (ids.isEmpty()) return
            context.sendBroadcast(
                Intent(context, SkyWidget::class.java)
                    .setAction(AppWidgetManager.ACTION_APPWIDGET_UPDATE)
                    .putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, ids),
            )
        }

        private fun refresh(context: Context, manager: AppWidgetManager, ids: IntArray) {
            val p = prefs(context)
            val hub = p.getString("hub", null)
            val key = p.getString("key", null)
            val views = RemoteViews(context.packageName, R.layout.sky_widget)
            val open = context.packageManager.getLaunchIntentForPackage(context.packageName)
            if (open != null) {
                views.setOnClickPendingIntent(
                    R.id.widget_root,
                    PendingIntent.getActivity(context, 0, open, PendingIntent.FLAG_IMMUTABLE),
                )
            }
            if (hub == null || key == null) {
                views.setTextViewText(R.id.widget_title, "allskyhub")
                views.setTextViewText(
                    R.id.widget_line,
                    "In der App bei einer Kamera „Als Widget“ wählen.",
                )
                views.setViewVisibility(R.id.widget_image, View.GONE)
                manager.updateAppWidget(ids, views)
                return
            }
            views.setTextViewText(R.id.widget_title, p.getString("name", "allskyhub"))
            try {
                val state = JSONObject(get("$hub/api/v1/widget", key).decodeToString())
                views.setTextViewText(R.id.widget_title, state.optString("name", "allskyhub"))
                views.setTextViewText(R.id.widget_line, line(state))
                image(hub, key)?.let {
                    views.setImageViewBitmap(R.id.widget_image, it)
                    views.setViewVisibility(R.id.widget_image, View.VISIBLE)
                }
            } catch (e: Unauthorized) {
                p.edit().remove("key").apply()
                views.setTextViewText(R.id.widget_line, "Kein Zugriff mehr. In der App neu wählen.")
            } catch (e: Exception) {
                views.setTextViewText(R.id.widget_line, "Hub nicht erreichbar.")
            }
            manager.updateAppWidget(ids, views)
        }

        private fun line(state: JSONObject): String {
            val parts = mutableListOf<String>()
            if (!state.optBoolean("online")) parts.add("offline")
            val at = state.optString("image_at", "")
            if (at.isNotEmpty() && at != "null") clock(at)?.let { parts.add("Bild $it") }
            if (!state.isNull("cloud_cover")) {
                parts.add("${Math.round(state.getDouble("cloud_cover") * 100)} % Wolken")
            }
            val tonight = state.optString("tonight", "")
            if (tonight.isNotEmpty() && tonight != "null") parts.add(tonight)
            return parts.joinToString(" · ")
        }

        private fun clock(iso: String): String? = try {
            val parser = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.ROOT)
            parser.timeZone = TimeZone.getTimeZone("UTC")
            val date = parser.parse(iso.take(19)) ?: return null
            SimpleDateFormat("HH:mm", Locale.GERMANY).format(date)
        } catch (e: Exception) {
            null
        }

        private fun image(hub: String, key: String): Bitmap? = try {
            val bytes = get("$hub/api/v1/widget/image.jpg", key)
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply {
                // RemoteViews carry bitmaps through a binder: keep them small.
                inSampleSize = 2
            })
        } catch (e: Exception) {
            null
        }

        private class Unauthorized : Exception()

        private fun get(url: String, key: String): ByteArray {
            val conn = URL(url).openConnection() as HttpURLConnection
            try {
                conn.connectTimeout = TIMEOUT_MS
                conn.readTimeout = TIMEOUT_MS
                conn.setRequestProperty("Authorization", "Bearer $key")
                when (conn.responseCode) {
                    200 -> return conn.inputStream.use { it.readBytes() }
                    401 -> throw Unauthorized()
                    else -> throw java.io.IOException("HTTP ${conn.responseCode}")
                }
            } finally {
                conn.disconnect()
            }
        }
    }
}
