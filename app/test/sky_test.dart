import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/gallery_screens.dart';
import 'package:allskyhub_app/screens/sky_charts.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'hub_client_test.dart' show cameraJson;

final skyJson = [
  {
    'at': '2026-10-08T19:00:00Z',
    'cloud_cover': null,
    'sqm_mag': null,
    'stars': null,
  },
  {
    'at': '2026-10-08T19:30:00Z',
    'cloud_cover': 0.9,
    'sqm_mag': null,
    'stars': 40,
  },
  {
    'at': '2026-10-08T20:00:00Z',
    'cloud_cover': 0.2,
    'sqm_mag': null,
    'stars': 600,
  },
  {
    'at': '2026-10-08T20:30:00Z',
    'cloud_cover': null,
    'sqm_mag': null,
    'stars': null,
  },
  {
    'at': '2026-10-08T21:00:00Z',
    'cloud_cover': 0.05,
    'sqm_mag': null,
    'stars': 900,
  },
];

void main() {
  test('gaps split the line, never zero', () {
    final samples = [for (final s in skyJson) SkySample.fromJson(s)];
    final segments = skySegments(samples, skySeries.first);
    expect(segments.length, 2);
    expect(segments.first.map((p) => p.$2), [90.0, 20.0]);
    expect(segments.last.single.$2, closeTo(5.0, 1e-9));
    expect(skySegments(samples, skySeries[1]), isEmpty); // no SQM: no chart
  });

  testWidgets('night view shows the charts that have data', (tester) async {
    tester.view.physicalSize = const Size(400, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final client = HubClient(
      baseUrl: 'https://hub.example',
      token: 'tok',
      httpClient: MockClient((request) async {
        final path = request.url.path;
        if (path.endsWith('/nights')) {
          return http.Response(
            jsonEncode([
              {
                'night_id': '20261008',
                'frames': 0,
                'first': null,
                'last': null,
                'sky': 5,
              },
            ]),
            200,
          );
        }
        if (path.endsWith('/sky')) {
          return http.Response(jsonEncode(skyJson), 200);
        }
        if (path.endsWith('/frames')) return http.Response('[]', 200);
        return http.Response('', 404);
      }),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: NightsScreen(client: client, camera: Camera.fromJson(cameraJson)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Himmelsdaten'), findsOneWidget);
    await tester.tap(find.text('Himmelsdaten'));
    await tester.pumpAndSettle();
    expect(find.text('Himmel in dieser Nacht'), findsOneWidget);
    expect(find.byKey(const ValueKey('sky-Bewölkung')), findsOneWidget);
    expect(find.byKey(const ValueKey('sky-Sterne')), findsOneWidget);
    expect(find.byKey(const ValueKey('sky-Himmelshelligkeit')), findsNothing);
    expect(find.textContaining('zuletzt 5 %'), findsOneWidget);
  });
}
