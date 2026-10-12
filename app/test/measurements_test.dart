import 'dart:convert';
import 'dart:math' as math;

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/measurements_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A night of samples every 10 minutes from 20:00 to 04:00 UTC, with a gap after midnight.
Map<String, dynamic> _series({String? night, int? hours}) {
  const from = 1791799200.0; // 2026-10-12 12:00 local-ish
  const to = from + 86400;
  final t = <double>[];
  final sqm = <double?>[];
  final cloud = <double?>[];
  final stars = <double?>[];
  for (var i = 0; i < 48; i++) {
    final ts = from + 8 * 3600 + i * 600;
    if (i == 24) {
      t.add(ts - 599);
      sqm.add(null);
      cloud.add(null);
      stars.add(null);
      continue;
    }
    t.add(ts);
    sqm.add(20.5 + math.sin(i / 8) * 0.6);
    cloud.add(40 + 30 * math.cos(i / 5));
    stars.add(300 + 200 * math.sin(i / 6));
  }
  final et = [for (var i = 0; i <= 288; i++) from + i * 300];
  return {
    'from': from,
    'to': to,
    'tz': 'Europe/Vienna',
    'night': hours == null ? (night ?? '20261012') : null,
    'hours': hours,
    'bucket_s': 0,
    'sky': {'t': t, 'sqm': sqm, 'cloud': cloud, 'stars': stars},
    'status': {
      't': t,
      'exposure': [for (final _ in t) 20.0],
      'gain': [for (final _ in t) 250.0],
      'sensor': [for (final _ in t) 12.0],
      'cpu': [for (final _ in t) 48.0],
      'disk': [for (final _ in t) 61.0],
    },
    'ephemeris': {
      't': et,
      'sun': [
        for (var i = 0; i <= 288; i++) 40 * math.cos(i / 288 * 2 * math.pi),
      ],
      'moon': [
        for (var i = 0; i <= 288; i++) 50 * math.sin(i / 288 * 2 * math.pi),
      ],
      'moon_illumination': [0.42],
    },
  };
}

void main() {
  testWidgets('shows the charts and switches the range', (tester) async {
    tester.view.physicalSize = const Size(420, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final asked = <String>[];
    final client = HubClient(
      baseUrl: 'https://hub.example',
      token: 'tok',
      httpClient: MockClient((request) async {
        asked.add(request.url.query);
        final q = request.url.queryParameters;
        return http.Response(
          jsonEncode(
            _series(
              night: q['night'],
              hours: q['hours'] == null ? null : int.parse(q['hours']!),
            ),
          ),
          200,
        );
      }),
    );
    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: MeasurementsScreen(client: client, cameraId: 'cam1'),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('chart-sqm')), findsOneWidget);
    expect(find.byKey(const Key('chart-cloud')), findsOneWidget);
    expect(find.byKey(const Key('chart-exposure')), findsOneWidget);
    expect(find.textContaining('Nacht 12./13.10.2026'), findsOneWidget);
    expect(find.textContaining('Mond 42 %'), findsOneWidget);

    await tester.tap(find.byKey(const Key('night-before')));
    await tester.pumpAndSettle();
    expect(asked.last, 'night=20261011');
    await tester.tap(find.byKey(const Key('range-168')));
    await tester.pumpAndSettle();
    expect(asked.last, 'hours=168');
  });

  test('nice ticks and night arithmetic', () {
    expect(niceTicks(19.6, 21.4), [20, 20.5, 21]);
    expect(shiftNight('20261231', 1), '20270101');
    expect(nightTitle('20261031'), 'Nacht 31./1.11.2026');
    expect(number(0.00125, 3), '0,00125');
  });
}
