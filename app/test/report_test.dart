import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/platform/location.dart';
import 'package:allskyhub_app/screens/cameras_screen.dart';
import 'package:allskyhub_app/screens/report_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _Here implements LocationSource {
  @override
  Future<LocationResult> current() async =>
      LocationResult.found(CameraLocation(48.1234, 14.4321));

  @override
  Future<String?> timezone() async => 'Europe/Vienna';
}

Map<String, Object?> _chart({bool ready = true}) => {
  'ready': ready,
  'reason': ready ? null : 'Es ist noch nicht dunkel genug.',
  'key': ready ? 'Cyg' : null,
  'name': ready ? 'Schwan' : null,
  'altitude': 52.0,
  'azimuth': 296.0,
  'stars': [
    [0, 0, 1.3],
    [3, -2, 4.2],
    [-4, 5, 6.8],
  ],
  'limits': [2, 3, 4, 4.5, 5, 5.5, 6, 6.5, 7],
  'sun_altitude': ready ? -30 : 10,
  'moon_up': false,
  'moon_illumination': 0.1,
  'next_at': null,
};

HubClient _hub(
  List<Object?> sent, {
  bool ready = true,
  String kind = 'owner',
}) => HubClient(
  baseUrl: 'https://hub.example',
  token: 'tok',
  httpClient: MockClient((request) async {
    http.Response json(Object body) =>
        http.Response.bytes(utf8.encode(jsonEncode(body)), 200);
    return switch ((request.method, request.url.path)) {
      ('GET', '/api/v1/reports/chart') => json(_chart(ready: ready)),
      ('POST', '/api/v1/reports') => () {
        sent.add(jsonDecode(request.body));
        return json({
          'sqm': 18.65,
          'label': 'Stadthimmel',
          'limit_mag': 4.5,
          'next_at': '2026-10-28T22:00:00Z',
        });
      }(),
      ('GET', '/api/v1/account/settings') => json({
        'event_keep_days': 30,
        'kind': kind,
      }),
      ('GET', '/api/v1/cameras') => json(<Object>[]),
      _ => http.Response('', 404),
    };
  }),
);

void main() {
  testWidgets('without an account: how to get one', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: ReportGateScreen(
          client: HubClient(baseUrl: 'https://hub.example'),
        ),
      ),
    );
    expect(find.text('Melder-Konto anlegen'), findsOneWidget);
  });

  testWidgets('find, compare, report', (tester) async {
    tester.view.physicalSize = const Size(800, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final sent = <Object?>[];
    await tester.pumpWidget(
      MaterialApp(
        home: ReportScreen(
          client: _hub(sent),
          location: _Here(),
          started: DateTime.now().subtract(const Duration(minutes: 20)),
        ),
      ),
    );
    await tester.pumpAndSettle(const Duration(seconds: 1));
    expect(find.textContaining('Schwan'), findsWidgets);
    expect(find.textContaining('dunkel genug'), findsOneWidget);
    await tester.tap(find.byKey(const Key('report-found')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('report-send')), findsOneWidget);
    await tester.tap(find.byKey(const Key('chart-4.5')));
    await tester.pump();
    await tester.tap(find.byKey(const Key('report-send')));
    await tester.pumpAndSettle();
    expect(sent.single, {
      'lat': 48.1,
      'lon': 14.4,
      'constellation': 'Cyg',
      'limit_mag': 4.5,
      'clouds': false,
    });
    expect(find.text('4,5 mag'), findsOneWidget);
    expect(find.textContaining('Stadthimmel'), findsOneWidget);
  });

  testWidgets('by day it says why not', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: ReportScreen(client: _hub([], ready: false), location: _Here()),
      ),
    );
    await tester.pumpAndSettle(const Duration(seconds: 1));
    expect(find.text('Es ist noch nicht dunkel genug.'), findsWidgets);
    expect(find.byKey(const Key('report-found')), findsNothing);
  });

  testWidgets('a reporter account cannot pair cameras', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: CamerasScreen(
          client: _hub([], kind: 'reporter'),
          onSignOut: () async {},
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Kamera koppeln'), findsNothing);
    expect(find.textContaining('Melder-Konto'), findsOneWidget);
  });
}
