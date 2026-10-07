import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/gallery_screens.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'hub_client_test.dart' show cameraJson;

const nightJson = {
  'night_id': '20261006',
  'frames': 2,
  'first': '2026-10-06T19:00:00Z',
  'last': '2026-10-07T04:30:00Z',
};

const framesJson = [
  {
    'name': 'image-1.jpg',
    'captured_at': '2026-10-06T19:00:00Z',
    'mode': 'night',
    'exposure_us': 30000000,
    'gain': 120.0,
    'sun_elevation': -12.5,
    'has_full': true,
  },
  {
    'name': 'image-2.jpg',
    'captured_at': '2026-10-07T04:30:00Z',
    'mode': 'night',
    'exposure_us': 20000000,
    'gain': 100,
    'sun_elevation': -8.0,
    'has_full': false,
  },
];

HubClient fakeHub({List<Object> nights = const [nightJson]}) => HubClient(
  baseUrl: 'https://hub.example',
  token: 'tok',
  httpClient: MockClient((request) async {
    final path = request.url.path;
    if (path.endsWith('/nights')) return http.Response(jsonEncode(nights), 200);
    if (path.endsWith('/frames')) {
      return http.Response(jsonEncode(framesJson), 200);
    }
    return http.Response('', 404); // images: the widgets show a placeholder
  }),
);

void main() {
  test('client parses nights and frames and builds archive URLs', () async {
    final client = fakeHub();
    final nights = await client.nights('cam');
    expect(nights.single.nightId, '20261006');
    expect(nights.single.evening, DateTime(2026, 10, 6));
    final frames = await client.frames('cam', '20261006');
    expect(frames.map((f) => f.hasFull), [true, false]);
    expect(frames.last.gain, 100);
    expect(
      client.frameUrl('cam', '20261006', 'image-1.jpg', thumb: true).path,
      '/api/v1/cameras/cam/frames/20261006/image-1.jpg/thumb.jpg',
    );
  });

  test('night titles span evening and morning', () {
    final night = Night.fromJson(nightJson);
    expect(nightTitle(night), 'Di 06.10. → Mi 07.10.');
    expect(exposure(30000000), '30.0 s');
    expect(exposure(800), '0.800 ms');
  });

  testWidgets('nights → grid → viewer with thumbnail fallback', (tester) async {
    final camera = Camera.fromJson(cameraJson);
    await tester.pumpWidget(
      MaterialApp(
        home: NightsScreen(client: fakeHub(), camera: camera),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Di 06.10. → Mi 07.10.'), findsOneWidget);
    expect(find.textContaining('2 Bilder'), findsOneWidget);

    await tester.tap(find.text('Di 06.10. → Mi 07.10.'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('image-1.jpg')), findsOneWidget);
    expect(find.byKey(const ValueKey('image-2.jpg')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('image-2.jpg')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Nur Vorschau'), findsOneWidget);
    expect(find.textContaining('20.0 s'), findsOneWidget);
  });

  testWidgets('empty archive explains itself', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: NightsScreen(
          client: fakeHub(nights: []),
          camera: Camera.fromJson(cameraJson),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('Noch keine Bilder im Archiv'), findsOneWidget);
  });
}
