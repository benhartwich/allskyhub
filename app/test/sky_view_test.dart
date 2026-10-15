import 'dart:async';
import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/sky_view_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

final camera = Camera(id: 'cam1', name: 'Garten', profile: 'sim', online: true);

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

void main() {
  test('gravity and the magnetic field give the pose', () {
    // Upright, facing north: up along +y; the field points north (−z) and down (−y).
    final pose = Pose.from((x: 0, y: 9.8, z: 0), (x: 0, y: -40, z: -20))!;
    expect(pose.looking.az, closeTo(0, 1e-9));
    expect(pose.looking.alt, closeTo(0, 1e-9));
    // East is to the right of the screen.
    final east = pose.toPhone((alt: 0, az: 90));
    expect(east.x, closeTo(1, 1e-9));
    expect(compassName(350), 'N');
    expect(compassName(135), 'SO');
  });

  testWidgets('sky view draws and says where the phone looks', (tester) async {
    final client = HubClient(
      baseUrl: 'https://hub.example',
      token: 'tok',
      httpClient: MockClient(
        (_) async => http.Response(
          jsonEncode({
            'at': '2026-10-14T21:00:00Z',
            'lines': [
              [
                [10, 350],
                [20, 10],
              ],
            ],
            'objects': [
              {
                'label': 'Kassiopeia',
                'kind': 'constellation',
                'alt': 30,
                'az': 5,
              },
              {'label': 'Jupiter', 'kind': 'planet', 'alt': 15, 'az': 355},
            ],
            'meteors': [
              {
                'at': '2026-10-14T20:30:00Z',
                'start': [25, 340],
                'end': [12, 20],
              },
            ],
            'overflights': [
              {
                'at': '2026-10-14T21:30:00Z',
                'start': [20, 300],
                'end': [40, 20],
                'kind': 'satellite',
                'name': 'ISS',
              },
            ],
          }),
          200,
        ),
      ),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: SkyViewScreen(
          client: client,
          camera: camera,
          poseSource: _FixedPose(),
          useCamera: false,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('sky-view')), findsOneWidget);
    expect(find.textContaining('Blick nach N (0°), 0° hoch'), findsOneWidget);
    // Without a camera (tests, no permission): the drawing alone, no camera switch.
    expect(find.byKey(const Key('sky-camera')), findsNothing);
    expect(find.byKey(const Key('sky-toggle-camera')), findsNothing);
    final painter =
        tester.widget<CustomPaint>(find.byKey(const Key('sky-view'))).painter!
            as SkyPainter;
    expect(painter.data.overflights.single.name, 'ISS');
    expect(painter.overflights, isTrue);
    await tester.tap(find.byKey(const Key('sky-toggle-overflights')));
    await tester.pump();
    final off =
        tester.widget<CustomPaint>(find.byKey(const Key('sky-view'))).painter!
            as SkyPainter;
    expect(off.overflights, isFalse);
  });
}
