import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/api/settings_schema.dart';
import 'package:allskyhub_app/platform/location.dart';
import 'package:allskyhub_app/screens/camera_settings_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'hub_client_test.dart' show cameraJson;

const current = {
  'latitude': 48.31,
  'longitude': 14.29,
  'timezone': 'Europe/Vienna',
  'camera': 'zwo-asi678mc',
  'day_delay_s': 30.0,
  'night_delay_s': 0.0,
};

class PhoneAt implements LocationSource {
  @override
  Future<LocationResult> current() async =>
      LocationResult.found(CameraLocation(47.81, 13.05));

  @override
  Future<String?> timezone() async => 'Europe/Berlin';
}

final schema = parseSchema([
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
    'key': 'night_max_exposure_s',
    'type': 'number',
    'label': {'de': 'Längste Belichtung nachts', 'en': 'Longest exposure'},
    'group': {'de': 'Belichtung', 'en': 'Exposure'},
    'unit': 's',
    'min': 1,
    'max': 300,
    'nullable': true,
  },
  {
    'key': 'timelapse_fps',
    'type': 'int',
    'label': 'Bilder pro Sekunde',
    'group': 'Nachtprodukte',
    'min': 1,
    'max': 60,
  },
  {
    'key': 'product_keogram',
    'type': 'bool',
    'label': 'Keogramm',
    'group': 'Nachtprodukte',
  },
  {
    'key': 'timelapse_quality',
    'type': 'choice',
    'label': 'Qualität',
    'group': 'Nachtprodukte',
    'choices': [
      {'value': 'small', 'label': 'klein'},
      {'value': 'high', 'label': 'hoch'},
    ],
  },
  {
    'key': 'mqtt_password',
    'type': 'string',
    'label': 'MQTT-Passwort',
    'group': 'MQTT',
    'nullable': true,
    'write_only': true,
  },
]);

const schemaCurrent = {
  'latitude': 48.31,
  'longitude': 14.29,
  'timezone': 'Europe/Vienna',
  'night_max_exposure_s': 60.0,
  'timelapse_fps': 25,
  'product_keogram': true,
  'timelapse_quality': 'small',
  'mqtt_password_set': true,
};

SettingsInput _unchanged() => SettingsInput()
  ..text.addAll({
    'latitude': '48.31',
    'longitude': '14,39',
    'timezone': 'Europe/Vienna',
    'night_max_exposure_s': '60',
    'timelapse_fps': '25',
    'mqtt_password': '',
  })
  ..switches['product_keogram'] = true
  ..choices['timelapse_quality'] = 'small';

void main() {
  test('the schema form sends only the changes (SPEC §6.9)', () {
    final (none, _) = schemaChanges(schema, schemaCurrent, _unchanged());
    expect(none, isEmpty);
    expect(schema[2].label, 'Längste Belichtung nachts');
    final input = _unchanged()
      ..text['night_max_exposure_s'] = ''
      ..text['timelapse_fps'] = '30'
      ..text['mqtt_password'] = 'geheim'
      ..switches['product_keogram'] = false
      ..choices['timelapse_quality'] = 'high';
    final (changes, _) = schemaChanges(schema, schemaCurrent, input);
    expect(changes, {
      'night_max_exposure_s': null, // emptied: back to the default
      'timelapse_fps': 30,
      'product_keogram': false,
      'timelapse_quality': 'high',
      'mqtt_password': 'geheim',
    });
    final (cleared, _) = schemaChanges(
      schema,
      schemaCurrent,
      _unchanged()..clear.add('mqtt_password'),
    );
    expect(cleared, {'mqtt_password': null});
  });

  test('the schema form checks numbers and the location', () {
    String? problem(void Function(SettingsInput) edit) {
      final input = _unchanged();
      edit(input);
      return schemaChanges(schema, schemaCurrent, input).$2;
    }

    expect(
      problem((i) => i.text['night_max_exposure_s'] = '400'),
      'Längste Belichtung nachts: bitte eine Zahl ab 1 bis 300 s.',
    );
    expect(
      problem((i) => i.text['timelapse_fps'] = '2.5'),
      'Bilder pro Sekunde: bitte eine ganze Zahl ab 1 bis 60.',
    );
    expect(
      problem((i) => i.text['longitude'] = ''),
      contains('Breite und Länge'),
    );
    expect(
      problem((i) => i.text['timezone'] = 'x' * 65),
      contains('64 Zeichen'),
    );
    expect(parseSchema(null), same(fallbackSchema));
  });

  testWidgets('sections from the schema, a secret is never shown', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(400, 2000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final sent = <Map<String, dynamic>>[];
    final client = HubClient(
      baseUrl: 'https://hub.example',
      token: 'tok',
      httpClient: MockClient((request) async {
        sent.add(jsonDecode(request.body) as Map<String, dynamic>);
        return http.Response(jsonEncode({'status': 'applied'}), 200);
      }),
    );
    final camera = Camera.fromJson({
      ...cameraJson,
      'status': {'settings': schemaCurrent},
      'settings_schema': [
        for (final key in [
          'location',
          'timezone',
          'product_keogram',
          'mqtt_password',
        ])
          {
            'location': {
              'key': 'location',
              'type': 'location',
              'label': 'Standort',
              'group': 'Standort',
            },
            'timezone': {
              'key': 'timezone',
              'type': 'string',
              'label': 'Zeitzone',
              'group': 'Standort',
            },
            'product_keogram': {
              'key': 'product_keogram',
              'type': 'bool',
              'label': 'Keogramm',
              'group': 'Nachtprodukte',
            },
            'mqtt_password': {
              'key': 'mqtt_password',
              'type': 'string',
              'label': 'MQTT-Passwort',
              'group': 'MQTT',
              'nullable': true,
              'write_only': true,
            },
          }[key],
      ],
    });
    await tester.pumpWidget(
      MaterialApp(
        home: CameraSettingsScreen(
          client: client,
          camera: camera,
          locationSource: PhoneAt(),
        ),
      ),
    );
    expect(find.text('48.31'), findsOneWidget); // the first section is open
    expect(find.text('Keogramm'), findsNothing); // the others are closed
    await tester.tap(find.text('Nachtprodukte'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('product_keogram')));
    await tester.tap(find.text('MQTT'));
    await tester.pumpAndSettle();
    expect(find.text('gesetzt – leer lassen zum Behalten'), findsOneWidget);
    await tester.tap(find.byKey(const Key('save')));
    await tester.pumpAndSettle();
    expect(sent.single, {'product_keogram': false});
  });

  testWidgets(
    'phone location prefills, save sends only the changes, hub error shown',
    (tester) async {
      tester.view.physicalSize = const Size(400, 1600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final sent = <Map<String, dynamic>>[];
      final client = HubClient(
        baseUrl: 'https://hub.example',
        token: 'tok',
        httpClient: MockClient((request) async {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          sent.add(body);
          return sent.length == 1
              ? http.Response(jsonEncode({'status': 'applied'}), 200)
              : http.Response(
                  jsonEncode({'detail': 'Die Kamera hat abgelehnt: Zeitzone.'}),
                  400,
                );
        }),
      );
      final camera = Camera.fromJson({
        ...cameraJson,
        'status': {
          ...(cameraJson['status'] as Map<String, Object?>),
          'settings': current,
        },
      });
      await tester.pumpWidget(
        MaterialApp(
          home: CameraSettingsScreen(
            client: client,
            camera: camera,
            locationSource: PhoneAt(),
          ),
        ),
      );
      expect(find.text('48.31'), findsOneWidget);

      await tester.tap(find.byKey(const Key('use-phone')));
      await tester.pumpAndSettle();
      expect(find.text('47.81'), findsOneWidget);
      await tester.tap(find.byKey(const Key('save')));
      await tester.pumpAndSettle();
      expect(sent.single, {
        'latitude': 47.81,
        'longitude': 13.05,
        'timezone': 'Europe/Berlin',
      });
      expect(find.textContaining('Gespeichert'), findsOneWidget);

      await tester.enterText(find.byKey(const Key('timezone')), 'Mars/Olympus');
      await tester.tap(find.byKey(const Key('save')));
      await tester.pumpAndSettle();
      expect(find.text('Die Kamera hat abgelehnt: Zeitzone.'), findsOneWidget);
    },
  );
}
