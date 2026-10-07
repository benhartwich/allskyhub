import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/camera_screen.dart';
import 'package:allskyhub_app/screens/camera_status.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'hub_client_test.dart' show cameraJson;

void main() {
  final now = DateTime.utc(2026, 10, 7, 22);

  test('ages read naturally', () {
    expect(formatAge(null), 'nie');
    expect(
      formatAge(now.subtract(const Duration(seconds: 20)), now: now),
      'gerade eben',
    );
    expect(
      formatAge(now.subtract(const Duration(minutes: 3)), now: now),
      'vor 3 Min.',
    );
    expect(
      formatAge(now.subtract(const Duration(hours: 2)), now: now),
      'vor 2 Std.',
    );
    expect(
      formatAge(now.subtract(const Duration(days: 1)), now: now),
      'vor 1 Tag',
    );
    expect(
      formatAge(now.subtract(const Duration(days: 4)), now: now),
      'vor 4 Tagen',
    );
  });

  test('exposure in s at night and ms by day', () {
    expect(formatExposure(30000000), '30.0 s');
    expect(formatExposure(250), '0.250 ms');
    expect(formatExposure(12000), '12.0 ms');
  });

  test('full status becomes rows with warnings', () {
    final rows = statusRows(
      status: {
        'mode': 'night',
        'exposure_us': 30000000,
        'gain': 120.4,
        'sensor_temp_c': 21.46,
        'cpu_temp_c': 82,
        'disk_free_pct': 7.6,
        'uptime_s': 3 * 86400,
        'time_trusted': false,
      },
      frame: {'sun_elevation': -35.24},
      lastSeen: now.subtract(const Duration(minutes: 2)),
      now: now,
    );
    String value(String label) =>
        rows.firstWhere((r) => r.label == label).value;
    bool warn(String label) => rows.firstWhere((r) => r.label == label).warning;

    expect(value('Zuletzt gesehen'), 'vor 2 Min.');
    expect(value('Modus'), 'Nacht');
    expect(value('Belichtung'), '30.0 s, Gain 120');
    expect(value('Sonnenhöhe'), '-35.2°');
    expect(value('Sensor'), '21.5 °C');
    expect(warn('Prozessor'), isTrue);
    expect(value('Speicher frei'), '8 %');
    expect(warn('Speicher frei'), isTrue);
    expect(value('Laufzeit'), '3 Tage');
    expect(warn('Uhrzeit'), isTrue);
  });

  test('missing fields are left out', () {
    final rows = statusRows(
      status: const {},
      frame: const {},
      lastSeen: null,
      now: now,
    );
    expect(rows.map((r) => r.label), ['Zuletzt gesehen']);
    expect(rows.single.value, 'nie');
  });

  testWidgets('camera screen shows live state and status rows', (tester) async {
    tester.view.physicalSize = const Size(
      400,
      1600,
    ); // a tall phone: image plus status
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final camera = Camera.fromJson({...cameraJson, 'latest_image_at': null});
    final client = HubClient(
      baseUrl: 'https://hub.example',
      token: 'tok',
      httpClient: MockClient(
        (_) async => http.Response(jsonEncode(cameraJson), 200),
      ),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: CameraScreen(client: client, camera: camera),
      ),
    );
    expect(find.text('Live'), findsOneWidget);
    expect(find.text('Status'), findsOneWidget);
    expect(find.text('Nacht'), findsOneWidget);
    expect(find.text('30.0 s, Gain 120'), findsOneWidget);
    expect(find.text('Warte auf das erste Bild …'), findsOneWidget);
    await tester.pumpWidget(const SizedBox()); // stops the refresh timer
  });
}
