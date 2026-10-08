import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import '../platform/location.dart';

const cameraChoices = {
  'auto': 'Automatisch erkennen',
  'zwo-asi678mc': 'ZWO ASI678MC',
  'rpi-hq': 'Raspberry Pi HQ Camera',
  'sim': 'Simulation (Test)',
};

String _num(Object? v) =>
    v is num ? (v == v.roundToDouble() ? '${v.round()}' : '$v') : '';

double? _parse(String text) =>
    double.tryParse(text.trim().replaceAll(',', '.'));

/// Changes between the camera's reported settings and the form (SPEC §6.5: only changed
/// keys are sent). Returns the changes, or a German error.
(Map<String, Object>?, String?) settingsChanges({
  required Map<String, dynamic> current,
  required String latitude,
  required String longitude,
  required String timezone,
  required String? camera,
  required String dayDelay,
  required String nightDelay,
}) {
  final changes = <String, Object>{};
  if (latitude.trim().isNotEmpty || longitude.trim().isNotEmpty) {
    final location = CameraLocation.tryParse(latitude, longitude);
    if (location == null) {
      return (null, 'Bitte Breite und Länge als gültige Zahlen angeben.');
    }
    if (location.latitude != current['latitude'] ||
        location.longitude != current['longitude']) {
      changes['latitude'] = location.latitude;
      changes['longitude'] = location.longitude;
    }
  }
  if (timezone.trim().isNotEmpty && timezone.trim() != current['timezone']) {
    changes['timezone'] = timezone.trim();
  }
  if (camera != null && camera != current['camera']) changes['camera'] = camera;
  for (final (key, text, label) in [
    ('day_delay_s', dayDelay, 'Pause am Tag'),
    ('night_delay_s', nightDelay, 'Pause in der Nacht'),
  ]) {
    if (text.trim().isEmpty) continue;
    final value = _parse(text);
    if (value == null || value < 0 || value > 3600) {
      return (null, '$label: bitte 0 bis 3600 Sekunden.');
    }
    if (value != current[key]) changes[key] = value;
  }
  return (changes, null);
}

/// "Standort ändern" and the other camera settings (roadmap #2).
class CameraSettingsScreen extends StatefulWidget {
  const CameraSettingsScreen({
    super.key,
    required this.client,
    required this.camera,
    required this.locationSource,
  });

  final HubClient client;
  final Camera camera;
  final LocationSource locationSource;

  @override
  State<CameraSettingsScreen> createState() => _CameraSettingsScreenState();
}

class _CameraSettingsScreenState extends State<CameraSettingsScreen> {
  late final Map<String, dynamic> _current =
      (widget.camera.status['settings'] as Map<String, dynamic>?) ?? const {};
  late final _latitude = TextEditingController(
    text: _num(_current['latitude']),
  );
  late final _longitude = TextEditingController(
    text: _num(_current['longitude']),
  );
  late final _timezone = TextEditingController(
    text: (_current['timezone'] as String?) ?? '',
  );
  late final _day = TextEditingController(text: _num(_current['day_delay_s']));
  late final _night = TextEditingController(
    text: _num(_current['night_delay_s']),
  );
  late String? _camera = _current['camera'] as String?;
  bool _busy = false;
  String? _message;
  bool _error = false;

  @override
  void dispose() {
    for (final c in [_latitude, _longitude, _timezone, _day, _night]) {
      c.dispose();
    }
    super.dispose();
  }

  void _show(String text, {bool error = false}) => setState(() {
    _message = text;
    _error = error;
  });

  Future<void> _usePhoneLocation() async {
    setState(() => _busy = true);
    final result = await widget.locationSource.current();
    final timezone = await widget.locationSource.timezone();
    if (!mounted) return;
    setState(() => _busy = false);
    final location = result.location;
    if (location == null) {
      _show(describeLocationProblem(result.problem!), error: true);
      return;
    }
    _latitude.text = location.latitude.toStringAsFixed(2);
    _longitude.text = location.longitude.toStringAsFixed(2);
    if (timezone != null) _timezone.text = timezone;
    _show('Standort des Handys übernommen. Zum Senden „Speichern“ tippen.');
  }

  Future<void> _save() async {
    final (changes, problem) = settingsChanges(
      current: _current,
      latitude: _latitude.text,
      longitude: _longitude.text,
      timezone: _timezone.text,
      camera: _camera,
      dayDelay: _day.text,
      nightDelay: _night.text,
    );
    if (problem != null) {
      _show(problem, error: true);
      return;
    }
    if (changes!.isEmpty) {
      _show('Nichts geändert.');
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.client.updateCameraSettings(widget.camera.id, changes);
      _show(
        'Gespeichert. Die Kamera startet die Aufnahme neu und meldet sich gleich wieder.',
      );
    } on HubException catch (e) {
      _show(e.message, error: true);
    } on Exception {
      _show('Der Hub ist nicht erreichbar.', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final online = widget.camera.online;
    return Scaffold(
      appBar: AppBar(title: Text('Einstellungen · ${widget.camera.name}')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          if (!online)
            Padding(
              padding: const EdgeInsets.only(bottom: 16),
              child: Text(
                'Die Kamera ist offline. Einstellungen gehen nur, wenn sie verbunden ist.',
                style: TextStyle(color: theme.colorScheme.error),
              ),
            ),
          if (_message != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 16),
              child: Text(
                _message!,
                key: const Key('message'),
                style: TextStyle(
                  color: _error
                      ? theme.colorScheme.error
                      : theme.colorScheme.primary,
                ),
              ),
            ),
          Text('Standort', style: theme.textTheme.titleMedium),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            key: const Key('use-phone'),
            onPressed: _busy ? null : _usePhoneLocation,
            icon: const Icon(Icons.my_location),
            label: const Text('Standort des Handys übernehmen'),
          ),
          Row(
            children: [
              Expanded(
                child: TextField(
                  key: const Key('latitude'),
                  controller: _latitude,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                    signed: true,
                  ),
                  decoration: const InputDecoration(labelText: 'Breite'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  key: const Key('longitude'),
                  controller: _longitude,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                    signed: true,
                  ),
                  decoration: const InputDecoration(labelText: 'Länge'),
                ),
              ),
            ],
          ),
          TextField(
            key: const Key('timezone'),
            controller: _timezone,
            decoration: const InputDecoration(
              labelText: 'Zeitzone',
              hintText: 'Europe/Vienna',
            ),
          ),
          const SizedBox(height: 24),
          Text('Kamera', style: theme.textTheme.titleMedium),
          DropdownButton<String>(
            key: const Key('camera'),
            isExpanded: true,
            value: cameraChoices.containsKey(_camera) ? _camera : null,
            hint: const Text('unverändert'),
            items: [
              for (final entry in cameraChoices.entries)
                DropdownMenuItem(value: entry.key, child: Text(entry.value)),
            ],
            onChanged: (value) => setState(() => _camera = value),
          ),
          const SizedBox(height: 24),
          Text(
            'Pause zwischen den Aufnahmen',
            style: theme.textTheme.titleMedium,
          ),
          Row(
            children: [
              Expanded(
                child: TextField(
                  key: const Key('day'),
                  controller: _day,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(labelText: 'Am Tag (s)'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  key: const Key('night'),
                  controller: _night,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(
                    labelText: 'In der Nacht (s)',
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 24),
          FilledButton(
            key: const Key('save'),
            onPressed: _busy || !online ? null : _save,
            child: const Text('Speichern'),
          ),
        ],
      ),
    );
  }
}
