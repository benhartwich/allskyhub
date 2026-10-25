// The camera's status as label/value rows (SPEC §6.3 `status`, §4.4 `frame`).

import '../l10n.dart';

/// "gerade eben", "vor 3 Min.", "vor 2 Std.", "vor 4 Tagen" ("just now", "3 min ago" …).
String formatAge(DateTime? time, {DateTime? now}) {
  if (time == null) return tr('nie', 'never');
  final seconds = (now ?? DateTime.now()).difference(time).inSeconds;
  if (seconds < 60) return tr('gerade eben', 'just now');
  if (seconds < 3600) {
    return tr('vor ${seconds ~/ 60} Min.', '${seconds ~/ 60} min ago');
  }
  if (seconds < 86400) {
    return tr('vor ${seconds ~/ 3600} Std.', '${seconds ~/ 3600} h ago');
  }
  final days = seconds ~/ 86400;
  return days == 1
      ? tr('vor 1 Tag', '1 day ago')
      : tr('vor $days Tagen', '$days days ago');
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
    StatusRow(
      tr('Zuletzt gesehen', 'Last seen'),
      formatAge(lastSeen, now: now),
    ),
  ];
  if (status['capture_paused'] == true) {
    // SPEC §4.1: paused in the camera's local page.
    rows.add(
      StatusRow(
        tr('Aufnahme', 'Capture'),
        tr('pausiert', 'paused'),
        warning: true,
      ),
    );
  }
  if (status['reboot_required'] == true) {
    // SPEC §8: system updates wait; the camera reboots by day on its own.
    rows.add(
      StatusRow(
        tr('Neustart', 'Restart'),
        tr('tagsüber für Updates', 'by day, for updates'),
      ),
    );
  }
  final mode = status['mode'];
  if (mode is String) {
    rows.add(
      StatusRow(
        tr('Modus', 'Mode'),
        mode == 'night' ? tr('Nacht', 'Night') : tr('Tag', 'Day'),
      ),
    );
  }
  final exposure = status['exposure_us'];
  final gain = status['gain'];
  if (exposure is num) {
    rows.add(
      StatusRow(
        tr('Belichtung', 'Exposure'),
        '${formatExposure(exposure)}${gain is num ? ', Gain ${gain.round()}' : ''}',
      ),
    );
  }
  final sun = frame['sun_elevation'];
  if (sun is num) {
    rows.add(
      StatusRow(tr('Sonnenhöhe', 'Sun altitude'), '${sun.toStringAsFixed(1)}°'),
    );
  }
  final sensor = status['sensor_temp_c'];
  if (sensor is num) {
    rows.add(StatusRow('Sensor', '${sensor.toStringAsFixed(1)} °C'));
  }
  final cpu = status['cpu_temp_c'];
  if (cpu is num) {
    rows.add(
      StatusRow(
        tr('Prozessor', 'Processor'),
        '${cpu.toStringAsFixed(1)} °C',
        warning: cpu >= 80,
      ),
    );
  }
  final disk = status['disk_free_pct'];
  if (disk is num) {
    rows.add(
      StatusRow(
        tr('Speicher frei', 'Free storage'),
        '${disk.round()} %',
        warning: disk < 10,
      ),
    );
  }
  final uptime = status['uptime_s'];
  if (uptime is num) {
    final hours = uptime ~/ 3600;
    rows.add(
      StatusRow(
        tr('Laufzeit', 'Uptime'),
        hours >= 48
            ? tr('${hours ~/ 24} Tage', '${hours ~/ 24} days')
            : tr('$hours Std.', '$hours h'),
      ),
    );
  }
  final orientation = status['orientation'];
  if (orientation is Map<String, dynamic> && orientation['north_deg'] is num) {
    final north = (orientation['north_deg'] as num).round() % 360;
    final stars = orientation['stars'];
    final rms = orientation['rms_deg'];
    rows.add(
      StatusRow(
        tr('Ausrichtung', 'Orientation'),
        '${tr('Norden bei $north°', 'North at $north°')}'
        '${stars is int ? tr(' · $stars Sterne', ' · $stars stars') : ''}'
        '${rms is num ? ' · ±${decimal(rms, 1)}°' : ''}',
      ),
    );
  }
  final update = status['update'];
  if (update is Map<String, dynamic>) {
    final line = updateLine(update);
    if (line != null) {
      rows.add(StatusRow('Software', line.$1, warning: line.$2));
    }
  }
  if (status['time_trusted'] == false) {
    rows.add(
      StatusRow(
        tr('Uhrzeit', 'Clock'),
        tr('nicht synchronisiert', 'not synchronized'),
        warning: true,
      ),
    );
  }
  return rows;
}

Map<String, String> get _updateStates => {
  'up_to_date': tr('aktuell', 'up to date'),
  'downloading': tr('Update {v} wird geladen', 'Downloading update {v}'),
  'waiting': tr(
    'Update {v} geladen, wird tagsüber installiert',
    'Update {v} downloaded, installs by day',
  ),
  'installing': tr('Update {v} wird installiert', 'Installing update {v}'),
  'installed': tr('Update {v} installiert', 'Update {v} installed'),
  'rolled_back': tr('Update {v} zurückgerollt', 'Update {v} rolled back'),
  'failed': tr('Update {v} fehlgeschlagen', 'Update {v} failed'),
};

Map<String, String> get _updateCodes => {
  'unhealthy': tr(
    'die neue Version lief nicht sauber',
    'the new version did not run cleanly',
  ),
  'checksum': tr('Prüfsumme falsch', 'wrong checksum'),
  'download': tr('Download fehlgeschlagen', 'download failed'),
  'bad_bundle': tr('Paket beschädigt', 'package damaged'),
  'bad_signature': tr('Signatur ungültig', 'invalid signature'),
  'bad_manifest': tr(
    'Update-Beschreibung ungültig',
    'invalid update description',
  ),
  'no_space': tr('zu wenig Speicher', 'not enough storage'),
};

/// Roadmap #7 `status.update`: the text and whether it is a warning.
(String, bool)? updateLine(Map<String, dynamic> update) {
  final state = update['state'];
  final template = state is String ? _updateStates[state] : null;
  if (template == null) return null;
  var text = template.replaceAll('{v}', '${update['version'] ?? ''}');
  final code = update['code'];
  if (code is String && code.isNotEmpty) {
    text += ' (${_updateCodes[code] ?? code})';
  }
  return (text, state == 'rolled_back' || state == 'failed');
}
