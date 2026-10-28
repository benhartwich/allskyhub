// Reminders for the calendar on "Himmel": a notification on the phone itself, no server and
// no Firebase. An all-day event (a shower maximum, a new moon) reminds at 18:00 local time
// that day, one with a time 30 minutes before. Opening "Himmel" again puts the newest
// forecast for that night into the reminder's text.

import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/data/latest.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;

import '../api/hub_client.dart';
import '../l10n.dart';

abstract class Reminders {
  /// Ids of the reminders still to come.
  Future<Set<int>> pending();

  /// False when the phone does not allow notifications or the time has passed.
  Future<bool> remind(CalendarItem item, {String? forecast});

  Future<void> forget(CalendarItem item);

  /// Puts the newest forecast (evening → summary) into the pending reminders among
  /// [items].
  Future<void> refresh(
    List<CalendarItem> items,
    Map<DateTime, String> forecasts,
  );
}

/// The evening of the night an event belongs to: the day before for early morning.
DateTime nightOf(CalendarItem item) {
  final local = item.at.toLocal();
  return DateTime(
    local.year,
    local.month,
    local.day - (local.hour < 12 ? 1 : 0),
  );
}

/// A stable id per event: kind and time.
int reminderId(CalendarItem item) =>
    Object.hash(item.kind, item.at.toUtc().millisecondsSinceEpoch) & 0x7fffffff;

/// When the reminder fires, or null when that is past.
DateTime? reminderTime(CalendarItem item, {DateTime? now}) {
  final local = item.at.toLocal();
  final at = item.allDay
      ? DateTime(local.year, local.month, local.day, 18)
      : local.subtract(const Duration(minutes: 30));
  return at.isAfter(now ?? DateTime.now()) ? at : null;
}

String reminderBody(CalendarItem item, String? forecast) => [
  item.text,
  if (forecast != null)
    tr('Vorhersage bei dir: $forecast', 'Forecast where you are: $forecast'),
].join('\n');

class LocalReminders implements Reminders {
  final _plugin = FlutterLocalNotificationsPlugin();
  bool _ready = false;

  static const _details = NotificationDetails(
    android: AndroidNotificationDetails(
      'sky_reminders',
      'Himmel',
      channelDescription: 'Erinnerungen an Ereignisse am Himmel',
      icon: '@drawable/ic_launcher_monochrome',
    ),
    iOS: DarwinNotificationDetails(),
  );

  Future<void> _init() async {
    if (_ready) return;
    tzdata.initializeTimeZones();
    await _plugin.initialize(
      settings: const InitializationSettings(
        android: AndroidInitializationSettings(
          '@drawable/ic_launcher_monochrome',
        ),
        // Ask for permission only when a bell is tapped (remind).
        iOS: DarwinInitializationSettings(
          requestAlertPermission: false,
          requestBadgePermission: false,
          requestSoundPermission: false,
        ),
      ),
    );
    _ready = true;
  }

  Future<bool> _allowed() async {
    final android = _plugin
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >();
    if (android != null) {
      return await android.requestNotificationsPermission() ?? false;
    }
    final ios = _plugin
        .resolvePlatformSpecificImplementation<
          IOSFlutterLocalNotificationsPlugin
        >();
    return await ios?.requestPermissions(alert: true, sound: true) ?? false;
  }

  @override
  Future<Set<int>> pending() async {
    try {
      await _init();
      return {
        for (final r in await _plugin.pendingNotificationRequests()) r.id,
      };
    } on Exception {
      return {};
    }
  }

  @override
  Future<bool> remind(CalendarItem item, {String? forecast}) async {
    final at = reminderTime(item);
    if (at == null) return false;
    try {
      await _init();
      if (!await _allowed()) return false;
      await _plugin.zonedSchedule(
        id: reminderId(item),
        scheduledDate: tz.TZDateTime.from(at.toUtc(), tz.UTC),
        notificationDetails: _details,
        androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
        title: item.title,
        body: reminderBody(item, forecast),
      );
      return true;
    } on Exception {
      return false;
    }
  }

  @override
  Future<void> forget(CalendarItem item) async {
    try {
      await _init();
      await _plugin.cancel(id: reminderId(item));
    } on Exception {
      // Nothing scheduled.
    }
  }

  @override
  Future<void> refresh(
    List<CalendarItem> items,
    Map<DateTime, String> forecasts,
  ) async {
    final pending = await this.pending();
    for (final item in items) {
      final forecast = forecasts[nightOf(item)];
      if (forecast != null && pending.contains(reminderId(item))) {
        await remind(item, forecast: forecast);
      }
    }
  }
}
