import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import '../api/settings_schema.dart';
import '../platform/location.dart';

String _num(Object? v) =>
    v is num ? (v == v.roundToDouble() ? '${v.round()}' : '$v') : '';

String _value(Object? v) => v is num ? _num(v) : (v is String ? v : '');

/// The camera's settings (roadmap #2), built from its settings schema (SPEC §6.9) like
/// on the web: every group a section, the first one open.
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
  late final List<SettingField> _schema = parseSchema(
    widget.camera.settingsSchema,
  );
  final _controllers = <String, TextEditingController>{};
  final _input = SettingsInput();
  bool _busy = false;
  String? _message;
  bool _error = false;

  @override
  void initState() {
    super.initState();
    for (final f in _schema) {
      switch (f.type) {
        case 'location':
          _controller('latitude', _num(_current['latitude']));
          _controller('longitude', _num(_current['longitude']));
        case 'bool':
          _input.switches[f.key] = _current[f.key] == true;
        case 'choice':
          final value = _current[f.key];
          _input.choices[f.key] = f.choices.any((c) => c.$1 == value)
              ? value as String
              : null;
        default:
          _controller(f.key, f.writeOnly ? '' : _value(_current[f.key]));
      }
    }
  }

  TextEditingController _controller(String key, String text) =>
      _controllers[key] = TextEditingController(text: text);

  @override
  void dispose() {
    for (final c in _controllers.values) {
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
    _controllers['latitude']!.text = location.latitude.toStringAsFixed(2);
    _controllers['longitude']!.text = location.longitude.toStringAsFixed(2);
    if (timezone != null) _controllers['timezone']?.text = timezone;
    _show('Standort des Handys übernommen. Zum Senden „Speichern“ tippen.');
  }

  Future<void> _save() async {
    for (final MapEntry(:key, :value) in _controllers.entries) {
      _input.text[key] = value.text;
    }
    final (changes, problem) = schemaChanges(_schema, _current, _input);
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
      for (final f in _schema.where((f) => f.writeOnly)) {
        _controllers[f.key]!.clear();
      }
      _input.clear.clear();
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

  Widget _field(BuildContext context, SettingField f) {
    final help = f.help;
    switch (f.type) {
      case 'location':
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            OutlinedButton.icon(
              key: const Key('use-phone'),
              onPressed: _busy ? null : _usePhoneLocation,
              icon: const Icon(Icons.my_location),
              label: const Text('Standort des Handys übernehmen'),
            ),
            Row(
              children: [
                for (final (key, label) in [
                  ('latitude', 'Breite'),
                  ('longitude', 'Länge'),
                ]) ...[
                  Expanded(
                    child: TextField(
                      key: Key(key),
                      controller: _controllers[key],
                      keyboardType: const TextInputType.numberWithOptions(
                        decimal: true,
                        signed: true,
                      ),
                      decoration: InputDecoration(labelText: label),
                    ),
                  ),
                  if (key == 'latitude') const SizedBox(width: 12),
                ],
              ],
            ),
          ],
        );
      case 'bool':
        return SwitchListTile(
          key: Key(f.key),
          contentPadding: EdgeInsets.zero,
          title: Text(f.label),
          subtitle: help == null ? null : Text(help),
          value: _input.switches[f.key] ?? false,
          onChanged: (on) => setState(() => _input.switches[f.key] = on),
        );
      case 'choice':
        return DropdownButtonFormField<String>(
          key: Key(f.key),
          isExpanded: true,
          initialValue: _input.choices[f.key],
          hint: const Text('unverändert'),
          decoration: InputDecoration(labelText: f.label, helperText: help),
          items: [
            for (final (value, label) in f.choices)
              DropdownMenuItem(value: value, child: Text(label)),
          ],
          onChanged: (value) => setState(() => _input.choices[f.key] = value),
        );
      default:
        final isSet = _current['${f.key}_set'] == true;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              key: Key(f.key),
              controller: _controllers[f.key],
              obscureText: f.writeOnly,
              autocorrect: !f.writeOnly,
              enableSuggestions: !f.writeOnly,
              maxLength: f.maxLength,
              keyboardType: f.numeric
                  ? TextInputType.numberWithOptions(
                      decimal: f.type == 'number',
                      signed: (f.min ?? 0) < 0,
                    )
                  : TextInputType.text,
              decoration: InputDecoration(
                labelText: f.label,
                helperText: help,
                helperMaxLines: 4,
                suffixText: f.unit,
                counterText: '',
                hintText: f.writeOnly
                    ? (isSet
                          ? 'gesetzt – leer lassen zum Behalten'
                          : 'nicht gesetzt')
                    : null,
              ),
            ),
            if (f.writeOnly && isSet)
              CheckboxListTile(
                key: Key('${f.key}-clear'),
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                title: Text('${f.label} löschen'),
                value: _input.clear.contains(f.key),
                onChanged: (on) => setState(
                  () => on == true
                      ? _input.clear.add(f.key)
                      : _input.clear.remove(f.key),
                ),
              ),
          ],
        );
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final online = widget.camera.online;
    final groups = <String, List<SettingField>>{};
    for (final f in _schema) {
      groups.putIfAbsent(f.group, () => []).add(f);
    }
    return Scaffold(
      appBar: AppBar(title: Text('Einstellungen · ${widget.camera.name}')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
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
          for (final (i, MapEntry(key: name, value: fields))
              in groups.entries.indexed)
            Card(
              margin: const EdgeInsets.only(bottom: 12),
              clipBehavior: Clip.antiAlias,
              child: ExpansionTile(
                key: Key('group-$name'),
                initiallyExpanded: i == 0,
                maintainState: true,
                title: Text(name, style: theme.textTheme.titleMedium),
                childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                children: [
                  for (final f in fields)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: _field(context, f),
                    ),
                ],
              ),
            ),
          const SizedBox(height: 12),
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
