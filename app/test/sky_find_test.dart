import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/sky_find.dart';
import 'package:allskyhub_app/screens/sky_view_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Held upright, the back of the phone toward the northern horizon.
const facingNorth = Pose((x: 1, y: 0, z: 0), (x: 0, y: 0, z: -1), (
  x: 0,
  y: 1,
  z: 0,
));

class _FixedPose implements PoseSource {
  @override
  Stream<Pose> get poses => Stream.value(facingNorth);
}

final _view = {
  'at': '2026-10-28T21:00:00Z',
  'lines': <Object>[],
  'objects': [
    {
      'label': 'Kassiopeia',
      'kind': 'constellation',
      'alt': 20,
      'az': 2,
      'key': 'Cas',
    },
    {
      'label': 'Saturn',
      'kind': 'planet',
      'alt': 35,
      'az': 180,
      'key': 'saturn',
    },
    {
      'label': 'M31 Andromedagalaxie',
      'kind': 's',
      'alt': 5,
      'az': 358,
      'key': 'M31',
      'mag': 3.4,
    },
    {
      'label': 'M57 Ringnebel',
      'kind': 'pn',
      'alt': 40,
      'az': 290,
      'key': 'M57',
      'mag': 9,
    },
  ],
  'meteors': <Object>[],
};

HubClient _hub() => HubClient(
  baseUrl: 'https://hub.example',
  httpClient: MockClient(
    (request) async => switch (request.url.path) {
      '/api/v1/public/sky-view' => http.Response.bytes(
        utf8.encode(jsonEncode(_view)),
        200,
      ),
      '/api/v1/public/sky-objects/M31' => http.Response.bytes(
        utf8.encode(
          jsonEncode({
            'key': 'M31',
            'kind': 's',
            'kind_label': 'Spiralgalaxie',
            'text': 'Unsere große Nachbargalaxie.',
            'photo': {
              'url': '/api/v1/public/sky-photos/M31',
              'author': 'NASA, ESA',
              'licence': 'Public domain',
              'licence_url': '',
              'page': 'https://commons.wikimedia.org/wiki/File:M31.jpg',
            },
          }),
        ),
        200,
      ),
      _ => http.Response('', 404),
    },
  ),
);

void main() {
  test('the guide says where to turn and when it is found', () {
    const north = (alt: 10.0, az: 0.0);
    expect(TargetCard.guide(north, (alt: 12.0, az: 2.0)), 'Gefunden!');
    expect(
      TargetCard.guide(north, (alt: 40.0, az: 90.0)),
      '30° höher und 90° nach rechts',
    );
    expect(TargetCard.guide(north, (alt: 10.0, az: 300.0)), '60° nach links');
    expect(
      worthIt((
        label: 'x',
        kind: 'pn',
        dir: (alt: 40, az: 0),
        key: 'M57',
        mag: 9,
      )),
      isFalse,
    );
    expect(
      worthIt((
        label: 'x',
        kind: 's',
        dir: (alt: 40, az: 0),
        key: 'M31',
        mag: 3.4,
      )),
      isTrue,
    );
  });

  testWidgets('search, guide, and the object card with its photo credit', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(800, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final client = _hub();
    await tester.pumpWidget(
      MaterialApp(
        home: SkyViewScreen(
          client: client,
          load: () => client.publicSkyView(48.1, 14.4),
          place: 'Linz',
          poseSource: _FixedPose(),
          useCamera: false,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('Linz'), findsOneWidget);
    // No meteors or overflights without a camera.
    expect(find.byKey(const Key('sky-toggle-overflights')), findsNothing);

    await tester.tap(find.byKey(const Key('sky-find')));
    await tester.pumpAndSettle();
    expect(find.text('Saturn'), findsOneWidget); // worth a look tonight
    expect(find.text('M57 Ringnebel'), findsNothing); // too faint for the list
    await tester.enterText(find.byKey(const Key('find-query')), 'ring');
    await tester.pump();
    await tester.tap(find.text('M57 Ringnebel'));
    await tester.pumpAndSettle();
    expect(find.text('Ziel: M57 Ringnebel'), findsOneWidget);
    expect(find.textContaining('nach links'), findsOneWidget);

    // Tapping M31, just left of the middle, opens its card.
    final view = tester.getRect(find.byKey(const Key('sky-view')));
    await tester.tapAt(view.center + const Offset(-47, -117));
    await tester.pumpAndSettle();
    expect(find.text('Unsere große Nachbargalaxie.'), findsOneWidget);
    expect(
      find.textContaining('Foto: NASA, ESA · Public domain'),
      findsOneWidget,
    );
    await tester.tap(find.byKey(const Key('guide-me')));
    await tester.pumpAndSettle();
    expect(find.text('Ziel: M31 Andromedagalaxie'), findsOneWidget);
    expect(find.text('Gefunden!'), findsOneWidget); // 5° from the middle
  });
}
