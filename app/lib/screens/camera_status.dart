// The camera's status as label/value rows (SPEC §6.3 `status`, §4.4 `frame`).

/// "gerade eben", "vor 3 Min.", "vor 2 Std.", "vor 4 Tagen".
String formatAge(DateTime? time, {DateTime? now}) {
  if (time == null) return 'nie';
  final seconds = (now ?? DateTime.now()).difference(time).inSeconds;
  if (seconds < 60) return 'gerade eben';
  if (seconds < 3600) return 'vor ${seconds ~/ 60} Min.';
  if (seconds < 86400) return 'vor ${seconds ~/ 3600} Std.';
  final days = seconds ~/ 86400;
  return days == 1 ? 'vor 1 Tag' : 'vor $days Tagen';
}

/// Exposure in seconds, or milliseconds for short daytime exposures.
String formatExposure(num microseconds) {
  if (microseconds >= 1e6) {
    return '${(microseconds / 1e6).toStringAsPrecision(3)} s';
  }
  return '${(microseconds / 1e3).toStringAsPrecision(3)} ms';
}

class StatusRow {
  const StatusRow(this.label, this.value, {this.warning = false});

  final String label;
  final String value;
  final bool warning;
}

/// Rows for the status card; fields the camera did not report are left out.
List<StatusRow> statusRows({
  required Map<String, dynamic> status,
  required Map<String, dynamic> frame,
  required DateTime? lastSeen,
  DateTime? now,
}) {
  final rows = <StatusRow>[
    StatusRow('Zuletzt gesehen', formatAge(lastSeen, now: now)),
  ];
  final mode = status['mode'];
  if (mode is String) {
    rows.add(StatusRow('Modus', mode == 'night' ? 'Nacht' : 'Tag'));
  }
  final exposure = status['exposure_us'];
  final gain = status['gain'];
  if (exposure is num) {
    rows.add(
      StatusRow(
        'Belichtung',
        '${formatExposure(exposure)}${gain is num ? ', Gain ${gain.round()}' : ''}',
      ),
    );
  }
  final sun = frame['sun_elevation'];
  if (sun is num) {
    rows.add(StatusRow('Sonnenhöhe', '${sun.toStringAsFixed(1)}°'));
  }
  final sensor = status['sensor_temp_c'];
  if (sensor is num) {
    rows.add(StatusRow('Sensor', '${sensor.toStringAsFixed(1)} °C'));
  }
  final cpu = status['cpu_temp_c'];
  if (cpu is num) {
    rows.add(
      StatusRow(
        'Prozessor',
        '${cpu.toStringAsFixed(1)} °C',
        warning: cpu >= 80,
      ),
    );
  }
  final disk = status['disk_free_pct'];
  if (disk is num) {
    rows.add(
      StatusRow('Speicher frei', '${disk.round()} %', warning: disk < 10),
    );
  }
  final uptime = status['uptime_s'];
  if (uptime is num) {
    final hours = uptime ~/ 3600;
    rows.add(
      StatusRow(
        'Laufzeit',
        hours >= 48 ? '${hours ~/ 24} Tage' : '$hours Std.',
      ),
    );
  }
  if (status['time_trusted'] == false) {
    rows.add(const StatusRow('Uhrzeit', 'nicht synchronisiert', warning: true));
  }
  return rows;
}
