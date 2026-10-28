import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/explore_screen.dart';
import 'package:allskyhub_app/screens/globe.dart';
import 'package:allskyhub_app/platform/location.dart';
import 'package:allskyhub_app/screens/join_screen.dart';
import 'package:allskyhub_app/screens/public_camera_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _camera = {
  'slug': 'abc',
  'title': 'Sternwarte',
  'place': 'Musterdorf',
  'lat': 50.0,
  'lon': 10.4,
  'online': true,
  'image_at': null,
  'night': true,
};

final _detail = {
  ..._camera,
  'location_label': 'Musterdorf',
  'camera': 'ZWO ASI678MC',
  'lens': '',
  'computer': '',
  'owner': 'Ben',
  'notes': '',
  'sections': <String, bool>{},
  'facts': {
    'mode': 'night',
    'sun_elevation': -30,
    'exposure_s': 20,
    'stars': 412,
  },
  'live_url': null,
  'page_url': '/sky/abc',
  'loop': <Object>[],
  'last_night': '20261027',
  'products': [
    {'name': 'keogram.jpg', 'url': '/sky/abc/products/20261027/keogram.jpg'},
  ],
  'video_url': null,
  'events': [
    {
      'slug': 'abc',
      'camera': 'Sternwarte',
      'kind': 'meteor',
      'title': 'Meteor',
      'at': '2026-10-28T01:02:03Z',
      'night_id': '20261027',
      'url': '/sky/abc/events/20261027/m1.jpg?v=1',
    },
  ],
  'nights': ['20261027'],
};

HubClient _hub({List<Object> cameras = const [_camera]}) => HubClient(
  baseUrl: 'https://hub.example',
  httpClient: MockClient(
    (request) async => switch (request.url.path) {
      '/api/v1/public/cameras' => http.Response(jsonEncode(cameras), 200),
      '/api/v1/public/feed' => http.Response(
        jsonEncode({'events': _detail['events']}),
        200,
      ),
      '/api/v1/public/cameras/abc' => http.Response(jsonEncode(_detail), 200),
      _ => http.Response('', 404),
    },
  ),
);

void _tall(WidgetTester tester) {
  tester.view.physicalSize = const Size(800, 3000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

void main() {
  testWidgets('lists the public cameras and searches them', (tester) async {
    _tall(tester);
    await tester.pumpWidget(MaterialApp(home: ExploreScreen(client: _hub())));
    await tester.pumpAndSettle();
    expect(find.byType(Globe), findsOneWidget);
    expect(find.text('Gerade passiert'), findsOneWidget);
    expect(find.text('Sternwarte'), findsOneWidget);

    await tester.enterText(find.byKey(const Key('explore-search')), 'xyz');
    await tester.pump();
    expect(find.text('Sternwarte'), findsNothing);
    expect(find.text('Nichts gefunden.'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('explore-search')), 'muster');
    await tester.pump();
    expect(find.text('Sternwarte'), findsOneWidget);
  });

  testWidgets('says when there is no public camera yet', (tester) async {
    _tall(tester);
    await tester.pumpWidget(
      MaterialApp(
        home: ExploreScreen(client: _hub(cameras: [])),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Noch keine öffentliche Kamera'),
      findsOneWidget,
    );
  });

  testWidgets('a public camera shows only what it publishes', (tester) async {
    _tall(tester);
    await tester.pumpWidget(
      MaterialApp(
        home: PublicCameraScreen(client: _hub(), slug: 'abc', title: 'x'),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Sternwarte'), findsOneWidget);
    expect(find.text('Kein öffentliches Livebild.'), findsOneWidget);
    expect(find.text('412'), findsOneWidget);
    expect(
      find.textContaining('Jedes Bild sammelt 20 s lang Licht'),
      findsOneWidget,
    );

    await tester.tap(find.byKey(const Key('tab-night')));
    await tester.pumpAndSettle();
    expect(find.text('Keogramm'), findsOneWidget);
    await tester.tap(find.byKey(const Key('tab-events')));
    await tester.pumpAndSettle();
    expect(find.text('Meteor'), findsOneWidget);
    await tester.tap(find.byKey(const Key('tab-about')));
    await tester.pumpAndSettle();
    expect(find.text('ZWO ASI678MC'), findsOneWidget);
    expect(find.text('Ben'), findsOneWidget);
  });

  testWidgets('own camera: guests can sign in from there', (tester) async {
    var asked = false;
    await tester.pumpWidget(
      MaterialApp(
        builder: (context, child) =>
            SignInScope(onSignIn: () => asked = true, child: child!),
        home: JoinScreen(client: _hub(), location: _NoPlace()),
      ),
    );
    await tester.tap(find.byKey(const Key('join-own-camera')));
    await tester.pumpAndSettle();
    expect(find.text('Anleitung und Hardware ansehen'), findsOneWidget);
    await tester.scrollUntilVisible(find.byKey(const Key('own-sign-in')), 100);
    await tester.tap(find.byKey(const Key('own-sign-in')));
    expect(asked, isTrue);
  });

  test('the sun stands over the tropic of Cancer at the June solstice', () {
    final sun = subsolarPoint(DateTime.utc(2026, 6, 21, 12));
    expect(sun.lat, closeTo(23.44, 0.1));
    expect(sun.lon, closeTo(0, 1.0)); // noon UTC: near Greenwich
  });

  test(
    'the globe projects its centre to the middle and hides the far side',
    () {
      final p = GlobeProjection(50, 10, const Offset(100, 100), 80);
      expect(p.project(50, 10), const Offset(100, 100));
      expect(p.project(-50, -170), isNull);
      expect(
        (p.projectClamped(0, -100) - const Offset(100, 100)).distance,
        closeTo(80, 1e-6),
      );
    },
  );
}

class _NoPlace implements LocationSource {
  @override
  Future<LocationResult> current() async =>
      const LocationResult.missing(LocationProblem.denied);

  @override
  Future<String?> timezone() async => null;
}
