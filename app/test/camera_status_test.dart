import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/platform/sky_widget.dart';
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

  test('orientation row when the camera is calibrated', () {
    final rows = statusRows(
      status: {
        'orientation': {
          'north_deg': 18.3,
          'mirrored': true,
          'stars': 34,
          'rms_deg': 0.3,
        },
      },
      frame: const {},
      lastSeen: null,
      now: now,
    );
    expect(
      rows.firstWhere((r) => r.label == 'Ausrichtung').value,
      'Norden bei 18° · 34 Sterne · ±0,3°',
    );
  });

  test('software update state', () {
    expect(updateLine({'state': 'up_to_date', 'version': '0.1.0'}), (
      'aktuell',
      false,
    ));
    expect(
      updateLine({
        'state': 'rolled_back',
        'version': '0.2.0',
        'code': 'unhealthy',
      }),
      ('Update 0.2.0 zurückgerollt (die neue Version lief nicht sauber)', true),
    );
    expect(updateLine({'state': 'bogus'}), isNull);
    final rows = statusRows(
      status: {
        'update': {'state': 'installed', 'version': '0.2.0'},
      },
      frame: const {},
      lastSeen: null,
      now: now,
    );
    expect(
      rows.firstWhere((r) => r.label == 'Software').value,
      'Update 0.2.0 installiert',
    );
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

  test('a paused capture is shown (SPEC §4.1)', () {
    final rows = statusRows(
      status: const {'capture_paused': true},
      frame: const {},
      lastSeen: null,
      now: now,
    );
    final paused = rows.firstWhere((r) => r.label == 'Aufnahme');
    expect((paused.value, paused.warning), ('pausiert', true));
  });

  test('a reboot for updates is shown (SPEC §8)', () {
    final rows = statusRows(
      status: const {'reboot_required': true},
      frame: const {},
      lastSeen: null,
      now: now,
    );
    final reboot = rows.firstWhere((r) => r.label == 'Neustart');
    expect((reboot.value, reboot.warning), ('tagsüber für Updates', false));
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

  testWidgets('camera screen shows health, tonight and last night', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(400, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final camera = Camera.fromJson({...cameraJson, 'latest_image_at': null});
    final client = HubClient(
      baseUrl: 'https://hub.example',
      token: 'tok',
      httpClient: MockClient((request) async {
        if (request.url.path.endsWith('/overview')) {
          return http.Response(
            jsonEncode({
              'online': true,
              'health': [
                {'key': 'disk', 'level': 'warn', 'text': 'Speicher fast voll.'},
              ],
              'tonight': {
                'summary': 'Klar und dunkel von 20:03 bis 03:00 Uhr.',
                'sunset': '2026-10-12T16:19:00Z',
                'dusk': '2026-10-12T18:03:00Z',
                'dawn': '2026-10-13T03:35:00Z',
                'sunrise': '2026-10-13T05:18:00Z',
                'moonrise': null,
                'moonset': '2026-10-12T16:33:00Z',
                'moon_illumination': 0.05,
                'moon_phase': 'zunehmende Sichel',
                'best_start': '2026-10-12T18:03:00Z',
                'best_end': '2026-10-13T01:00:00Z',
                'hours': [
                  {
                    'at': '2026-10-12T18:00:00Z',
                    'clouds': 5,
                    'moon_up': false,
                    'good': true,
                  },
                  {
                    'at': '2026-10-12T19:00:00Z',
                    'clouds': 80,
                    'moon_up': false,
                    'good': false,
                  },
                ],
              },
              'nowcast': {
                'cover': 0.8,
                'summary': 'Es klart in etwa 30 Minuten auf.',
                'wind': 'Wind aus Westen, 40 km/h',
                'steps': [
                  {
                    'minutes': 30,
                    'cover': 0.1,
                    'label': 'Kamera 20 km westlich',
                  },
                ],
              },
              'last_night': {
                'night_id': '20261011',
                'line': '3 Meteore, 4 h klar',
                'meteors': 3,
                'clear_hours': 4.0,
                'best_sqm': 21.2,
              },
            }),
            200,
          );
        }
        return http.Response(jsonEncode(cameraJson), 200);
      }),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: CameraScreen(client: client, camera: camera),
      ),
    );
    await tester.pumpAndSettle(const Duration(milliseconds: 100));
    expect(find.text('Speicher fast voll.'), findsOneWidget);
    expect(find.text('Heute Nacht'), findsOneWidget);
    expect(find.textContaining('Klar und dunkel'), findsOneWidget);
    expect(find.textContaining('zunehmende Sichel, 5 %'), findsOneWidget);
    expect(find.text('3 Meteore, 4 h klar'), findsOneWidget);
    expect(
      find.text('Jetzt 80 % Wolken. Es klart in etwa 30 Minuten auf.'),
      findsOneWidget,
    );
    expect(find.textContaining('Kamera 20 km westlich'), findsOneWidget);
    expect(find.byKey(const Key('open-measurements')), findsOneWidget);
    await tester.pumpWidget(const SizedBox()); // stops the refresh timer
  });

  testWidgets('camera screen puts the camera on the home-screen widget', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(400, 2400);
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
    final channel = _FakeWidget();
    await tester.pumpWidget(
      MaterialApp(
        home: CameraScreen(
          client: client,
          camera: camera,
          widgetChannel: channel,
        ),
      ),
    );
    await tester.pump();
    final button = find.byKey(const Key('as-widget'));
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pump();
    expect(channel.shown, [camera.id]);
    expect(find.textContaining('„Widgets“ wählen'), findsOneWidget);
    await tester.pumpWidget(const SizedBox()); // stops the refresh timer
  });
}

class _FakeWidget implements SkyWidgetChannel {
  final shown = <String>[];

  @override
  bool get available => true;

  @override
  Future<WidgetResult> show(HubClient client, Camera camera) async {
    shown.add(camera.id);
    return WidgetResult.manual;
  }
}
