import 'dart:convert';
import 'dart:typed_data';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/platform/location.dart';
import 'package:allskyhub_app/platform/push.dart';
import 'package:allskyhub_app/screens/sightings_screen.dart';
import 'package:allskyhub_app/screens/sky_view_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const facingNorth = Pose((x: 1, y: 0, z: 0), (x: 0, y: 0, z: -1), (
  x: 0,
  y: 1,
  z: 0,
));

class _FixedPose implements PoseSource {
  @override
  Stream<Pose> get poses => Stream.value(facingNorth);
}

class _Here implements LocationSource {
  @override
  Future<LocationResult> current() async =>
      LocationResult.found(CameraLocation(48.31, 14.29));

  @override
  Future<String?> timezone() async => 'Europe/Vienna';
}

http.Response _json(Object body) =>
    http.Response.bytes(utf8.encode(jsonEncode(body)), 200);

final _aurora = {
  'id': 'a1',
  'at': '2026-10-28T21:00:00Z',
  'distance_km': 25,
  'naked_eye': true,
  'details': ['rays'],
  'photo': '/api/v1/sightings/aurora/a1/photo.jpg',
  'thumb': '/api/v1/sightings/aurora/a1/photo.jpg?thumb=1',
  'author': 'Ben',
  'photo_state': null,
};

// A 1×1 PNG.
final _png = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
);

HubClient _hub(List<http.Request> seen) => HubClient(
  baseUrl: 'https://hub.example',
  token: 'tok',
  httpClient: MockClient((request) async {
    seen.add(request);
    return switch ((request.method, request.url.path)) {
      ('POST', '/api/v1/sightings/fireball') => _json({
        'id': 'f1',
        'cameras': [
          {'at': '2026-10-28T21:14:08Z', 'title': 'Lanzenberg', 'image': null},
          {'at': '2026-10-28T21:14:09Z', 'title': null, 'image': null},
        ],
        'tracks': ['/feuerkugeln/p1'],
        'others_km': [50, 100],
      }),
      ('POST', '/api/v1/sightings/aurora') => _json({
        ..._aurora,
        'photo': null,
        'thumb': null,
        'author': null,
        'photo_state': 'pending',
      }),
      ('GET', '/api/v1/sightings/aurora') => _json([_aurora]),
      ('POST', '/api/v1/sightings/aurora/a1/flag') => http.Response('', 204),
      ('GET', '/api/v1/account/alerts') => _json({
        'lat': null,
        'lon': null,
        'radius_km': 300,
        'aurora': false,
        'fireball': false,
        'clear_night': false,
        'tz': null,
      }),
      ('PUT', '/api/v1/account/alerts') => _json({
        ...jsonDecode(request.body) as Map<String, dynamic>,
        'radius_km': 300,
        'fireball': false,
        'clear_night': false,
      }),
      ('GET', '/api/v1/account/settings') => _json({
        'event_keep_days': 30,
        'push_available': false,
      }),
      _ => http.Response('', 404),
    };
  }),
);

void _tall(WidgetTester tester) {
  tester.view.physicalSize = const Size(800, 2400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

void main() {
  testWidgets('a fireball: point at start, pick brightness, see the matches', (
    tester,
  ) async {
    _tall(tester);
    final seen = <http.Request>[];
    await tester.pumpWidget(
      MaterialApp(
        home: FireballScreen(
          client: _hub(seen),
          location: _Here(),
          poseSource: _FixedPose(),
        ),
      ),
    );
    await tester.tap(find.byKey(const Key('fireball-start')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('point-here')));
    await tester.pumpAndSettle();
    expect(find.text('N, 0°'), findsOneWidget);
    await tester.tap(find.byKey(const Key('bright-venus')));
    await tester.tap(find.byKey(const Key('green')));
    await tester.pump();
    await tester.tap(find.byKey(const Key('fireball-send')));
    await tester.pumpAndSettle();
    final body = jsonDecode(seen.single.body) as Map<String, dynamic>;
    expect(body['lat'], 48.3);
    expect(body['start'], [0.0, 0.0]);
    expect(body['brightness'], 'venus');
    expect(body['details'], ['green']);
    expect(find.text('2 Kameras um diese Zeit'), findsOneWidget);
    expect(find.text('Lanzenberg'), findsOneWidget);
    expect(find.textContaining('50 km, 100 km entfernt'), findsOneWidget);
  });

  testWidgets('aurora with a photo needs the licence, then the list', (
    tester,
  ) async {
    _tall(tester);
    final seen = <http.Request>[];
    await tester.pumpWidget(
      MaterialApp(
        home: AuroraScreen(
          client: _hub(seen),
          location: _Here(),
          pickPhoto: () async => (Uint8List.fromList(_png), 'a.png'),
        ),
      ),
    );
    await tester.tap(find.byKey(const Key('aurora-eye-true')));
    await tester.tap(find.byKey(const Key('rays')));
    await tester.tap(find.byKey(const Key('aurora-photo')));
    await tester.pumpAndSettle();
    final send = find.byKey(const Key('aurora-send'));
    expect(
      tester.widget<FilledButton>(send).onPressed,
      isNull,
    ); // no licence yet
    await tester.enterText(find.byKey(const Key('aurora-author')), 'Ben');
    await tester.tap(find.byKey(const Key('aurora-licence')));
    await tester.pump();
    await tester.tap(send);
    await tester.pumpAndSettle();
    final post = latin1.decode(
      seen.firstWhere((r) => r.method == 'POST').bodyBytes,
    );
    for (final (name, value) in [
      ('naked_eye', 'true'),
      ('details', 'rays'),
      ('author', 'Ben'),
      ('licence', 'true'),
    ]) {
      expect(post, contains('name="$name"\r\n\r\n$value'));
    }
    expect(post, contains('name="photo"; filename="a.png"'));
    expect(find.textContaining('sobald wir es geprüft haben'), findsOneWidget);
    expect(find.textContaining('Foto: Ben · CC BY 4.0'), findsOneWidget);
    expect(find.textContaining('25 km'), findsOneWidget);

    await tester.tap(find.byKey(const Key('flag-a1')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Melden'));
    await tester.pumpAndSettle();
    expect(seen.where((r) => r.url.path.endsWith('/flag')), hasLength(1));
    expect(find.byIcon(Icons.flag), findsOneWidget);
  });

  testWidgets('alerts: switching one on sends the place and zone', (
    tester,
  ) async {
    final push = NoPush();
    pushService = push;
    addTearDown(() => pushService = FirebasePush());
    final seen = <http.Request>[];
    await tester.pumpWidget(
      MaterialApp(
        home: AlertsScreen(client: _hub(seen), location: _Here()),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('noch nicht eingerichtet'), findsOneWidget);
    await tester.tap(find.byKey(const Key('alert-aurora')));
    await tester.pumpAndSettle();
    final put = seen.firstWhere((r) => r.method == 'PUT');
    expect(jsonDecode(put.body), {
      'aurora': true,
      'lat': 48.31,
      'lon': 14.29,
      'tz': 'Europe/Vienna',
    });
    expect(push.registered, 1); // the phone registers for push
  });
}
