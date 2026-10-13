// The camera's settings form (SPEC §6.9): built from `settings_schema` like on the web
// (server devices/settings_form.py), checked here first, checked again by the camera.

import '../platform/location.dart';

/// A schema text: a plain string or `{de, en}`; German is the fallback.
String _text(Object? value) => switch (value) {
  String s => s,
  Map<String, dynamic> m => (m['de'] as String?) ?? '',
  _ => '',
};

double? parseNumber(String text) =>
    double.tryParse(text.trim().replaceAll(',', '.'));

/// One entry of `settings_schema`.
class SettingField {
  SettingField.fromJson(Map<String, dynamic> json)
    : key = json['key'] as String,
      type = json['type'] as String,
      label = _text(json['label']),
      group = _text(json['group']),
      help = json['help'] == null ? null : _text(json['help']),
      unit = json['unit'] as String?,
      min = (json['min'] as num?)?.toDouble(),
      max = (json['max'] as num?)?.toDouble(),
      choices = [
        for (final c in (json['choices'] as List<dynamic>?) ?? const [])
          ((c as Map<String, dynamic>)['value'] as String, _text(c['label'])),
      ],
      maxLength = json['max_length'] as int?,
      nullable = json['nullable'] as bool? ?? false,
      writeOnly = json['write_only'] as bool? ?? false;

  final String key;

  /// number, int, bool, choice, string or location.
  final String type;
  final String label;
  final String group;
  final String? help;
  final String? unit;
  final double? min;
  final double? max;
  final List<(String, String)> choices;
  final int? maxLength;
  final bool nullable;

  /// A secret: never reported, only `<key>_set`; an empty field keeps it.
  final bool writeOnly;

  bool get numeric => type == 'number' || type == 'int';
}

/// The fixed fields of SPEC §6.5, for a hub that sends no schema.
final List<SettingField> fallbackSchema = [
  for (final json in <Map<String, dynamic>>[
    {
      'key': 'location',
      'type': 'location',
      'label': 'Standort',
      'group': 'Standort',
    },
    {
      'key': 'timezone',
      'type': 'string',
      'label': 'Zeitzone',
      'group': 'Standort',
      'max_length': 64,
    },
    {
      'key': 'camera',
      'type': 'choice',
      'label': 'Kamera',
      'group': 'Kamera',
      'choices': [
        {'value': 'auto', 'label': 'Automatisch erkennen'},
        {'value': 'zwo-asi678mc', 'label': 'ZWO ASI678MC'},
        {'value': 'rpi-hq', 'label': 'Raspberry Pi HQ Camera'},
        {'value': 'sim', 'label': 'Simulation (Test)'},
      ],
    },
    {
      'key': 'day_delay_s',
      'type': 'number',
      'label': 'Pause am Tag',
      'group': 'Aufnahme',
      'unit': 's',
      'min': 0,
      'max': 3600,
    },
    {
      'key': 'night_delay_s',
      'type': 'number',
      'label': 'Pause in der Nacht',
      'group': 'Aufnahme',
      'unit': 's',
      'min': 0,
      'max': 3600,
    },
  ])
    SettingField.fromJson(json),
];

List<SettingField> parseSchema(Object? raw) {
  if (raw is! List || raw.isEmpty) return fallbackSchema;
  try {
    return [
      for (final s in raw) SettingField.fromJson(s as Map<String, dynamic>),
    ];
  } on Object {
    return fallbackSchema;
  }
}

/// What the user entered: text per key (numbers, strings, secrets, and `latitude` /
/// `longitude`), switches, choices and secrets to delete.
class SettingsInput {
  final text = <String, String>{};
  final switches = <String, bool>{};
  final choices = <String, String?>{};
  final clear = <String>{};
}

String _fmt(double v) => v == v.roundToDouble() ? '${v.round()}' : '$v';

/// The keys that differ from the camera's values (SPEC §6.5: only changes are sent), or a
/// German error. Empty fields stay unchanged; a nullable field emptied becomes null.
(Map<String, Object?>?, String?) schemaChanges(
  List<SettingField> schema,
  Map<String, dynamic> current,
  SettingsInput input,
) {
  final changes = <String, Object?>{};
  for (final f in schema) {
    final key = f.key;
    switch (f.type) {
      case 'location':
        final lat = input.text['latitude'] ?? '';
        final lon = input.text['longitude'] ?? '';
        if (lat.trim().isEmpty && lon.trim().isEmpty) continue;
        final location = CameraLocation.tryParse(lat, lon);
        if (location == null) {
          return (null, 'Bitte Breite und Länge als gültige Zahlen angeben.');
        }
        if (location.latitude != current['latitude'] ||
            location.longitude != current['longitude']) {
          changes['latitude'] = location.latitude;
          changes['longitude'] = location.longitude;
        }
      case 'bool':
        final on = input.switches[key];
        if (on != null && on != (current[key] == true)) changes[key] = on;
      case 'choice':
        final value = input.choices[key];
        if (value != null && value != current[key]) changes[key] = value;
      default:
        final text = (input.text[key] ?? '').trim();
        if (f.writeOnly) {
          if (input.clear.contains(key)) {
            changes[key] = null;
          } else if (text.isNotEmpty) {
            changes[key] = text;
          }
          continue;
        }
        if (text.isEmpty) {
          if (f.nullable && current[key] != null) changes[key] = null;
          continue;
        }
        if (f.numeric) {
          final value = parseNumber(text);
          final range = [
            if (f.min != null) 'ab ${_fmt(f.min!)}',
            if (f.max != null) 'bis ${_fmt(f.max!)}',
          ].join(' ');
          final unit = f.unit == null ? '' : ' ${f.unit}';
          if (value == null ||
              (f.type == 'int' && value != value.roundToDouble()) ||
              (f.min != null && value < f.min!) ||
              (f.max != null && value > f.max!)) {
            final hint = range.isEmpty ? '' : ' $range$unit';
            return (
              null,
              '${f.label}: bitte eine ${f.type == 'int' ? 'ganze ' : ''}Zahl$hint.',
            );
          }
          final number = f.type == 'int' ? value.round() : value;
          if (number != current[key]) changes[key] = number;
        } else {
          if (f.maxLength != null && text.length > f.maxLength!) {
            return (null, '${f.label}: höchstens ${f.maxLength} Zeichen.');
          }
          if (text != current[key]) changes[key] = text;
        }
    }
  }
  return (changes, null);
}
