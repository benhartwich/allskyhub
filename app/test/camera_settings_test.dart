import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/platform/location.dart';
import 'package:allskyhub_app/screens/camera_settings_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'hub_client_test.dart' show cameraJson;

const current = {
  'latitude': 48.14,
  'longitude': 14.39,
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

void main() {
  test('only changed keys are sent; location goes as a pair', () {
    final (unchanged, _) = settingsChanges(
      current: current,
      latitude: '48.14',
      longitude: '14,39',
      timezone: 'Europe/Vienna',
      camera: 'zwo-asi678mc',
      dayDelay: '30',
      nightDelay: '0',
    );
    expect(unchanged, isEmpty);
    final (changes, _) = settingsChanges(
      current: current,
      latitude: '48.14',
      longitude: '14.5',
      timezone: 'Europe/Vienna',
      camera: 'rpi-hq',
      dayDelay: '60',
      nightDelay: '',
    );
    expect(changes, {
      'latitude': 48.14,
      'longitude': 14.5,
      'camera': 'rpi-hq',
      'day_delay_s': 60.0,
    });
    final (_, bad) = settingsChanges(
      current: current,
      latitude: '48',
      longitude: '',
      timezone: '',
      camera: null,
      dayDelay: '',
      nightDelay: '',
    );
    expect(bad, contains('Breite und Länge'));
    final (_, slow) = settingsChanges(
      current: current,
      latitude: '',
      longitude: '',
      timezone: '',
      camera: null,
      dayDelay: '5000',
      nightDelay: '',
    );
    expect(slow, contains('0 bis 3600'));
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
      expect(find.text('48.14'), findsOneWidget);

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
