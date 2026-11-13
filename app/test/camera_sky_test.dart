import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/camera_sky.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Widget _host(Widget child) => MaterialApp(
  home: Scaffold(body: SingleChildScrollView(child: child)),
);

const _overlayJson = {
  'width': 1200,
  'height': 1200,
  'font': 17.1,
  'lines': 'M100.0 100.0L200.0 200.0L300.0 150.0 M500 500L520 540',
  'names': [
    {'x': 150, 'y': 150, 'text': 'Pegasus'},
  ],
  'cardinals': [
    {'x': 600, 'y': 20, 'text': 'N'},
  ],
  'stars': [
    {'x': 892.0, 'y': 520.0, 'text': 'Vega', 'trail': ''},
  ],
  'grid': ['M0 0L10 10'],
  'grid_labels': [
    {'x': 600.0, 'y': 300.0, 'text': '30°', 'trail': ''},
  ],
  'grid_cardinals': [
    {'x': 600.0, 'y': 40.0, 'text': 'N', 'trail': ''},
  ],
  'planets': [
    {'x': 456.0, 'y': 880.0, 'text': 'Saturn', 'trail': ''},
  ],
  'satellites': [
    {
      'x': 522.0,
      'y': 775.0,
      'text': 'Raumstation ISS',
      'trail': 'M782.8 854.5L749.3 848.4',
    },
  ],
  'tracks': <Object>[],
  'at': '2026-10-19T04:33:08+00:00',
  'aircraft': <Object>[],
  'aircraft_credit': 'Aircraft data: adsb.lol (ODbL)',
};

void main() {
  test('SVG paths of M and L become polylines', () {
    expect(parseSvgPath('M1 2L3 4L5.5 -6 M7,8L9 10'), [
      [(1.0, 2.0), (3.0, 4.0), (5.5, -6.0)],
      [(7.0, 8.0), (9.0, 10.0)],
    ]);
    expect(parseSvgPath(''), isEmpty);
  });

  test('the overlay from the hub', () {
    final o = SkyOverlay.fromJson(
      jsonDecode(jsonEncode(_overlayJson)) as Map<String, dynamic>,
    );
    expect((o.width, o.height), (1200.0, 1200.0));
    expect(o.lines, hasLength(2));
    expect(o.stars.single.text, 'Vega');
    expect(o.satellites.single.trail, [(782.8, 854.5), (749.3, 848.4)]);
    expect(o.gridLabels.map((m) => m.text), ['30°', 'N']);
  });

  testWidgets('the layers paint and switch', (tester) async {
    final overlay = SkyOverlay.fromJson(
      jsonDecode(jsonEncode(_overlayJson)) as Map<String, dynamic>,
    );
    shownLayers.value = {};
    await tester.pumpWidget(
      _host(
        Column(
          children: [
            const LayerChips(),
            SizedBox(
              width: 300,
              height: 300,
              child: ValueListenableBuilder<Set<SkyLayer>>(
                valueListenable: shownLayers,
                builder: (_, shown, _) =>
                    CustomPaint(painter: SkyOverlayPainter(overlay, shown)),
              ),
            ),
          ],
        ),
      ),
    );
    await tester.tap(find.byKey(const Key('layer-satellites')));
    await tester.tap(find.byKey(const Key('layer-stars')));
    await tester.pump();
    expect(shownLayers.value, {SkyLayer.satellites, SkyLayer.stars});
    for (final l in SkyLayer.values) {
      shownLayers.value = {l};
      await tester.pump();
    }
    expect(tester.takeException(), isNull);
    shownLayers.value = {};
  });

  testWidgets('orientation line and the sky tiles from the status', (
    tester,
  ) async {
    final status = {
      'orientation': {
        'north_deg': 78.4,
        'mirrored': true,
        'stars': 412,
        'rms_deg': 0.08,
        'solved_at': DateTime.now()
            .subtract(const Duration(hours: 3))
            .toUtc()
            .toIso8601String(),
      },
      'sky': {
        'at': DateTime.now().toUtc().toIso8601String(),
        'stars': 412,
        'cloud_cover': 0.083,
        'sqm_mag': 20.63,
        'transparency': 0.12,
        'cloud_map': [
          0.9,
          0.8,
          0.2,
          0,
          0,
          null,
          0.1,
          0.6,
          ...List.filled(16, 0),
        ],
      },
    };
    await tester.pumpWidget(
      _host(
        Column(
          children: [
            OrientationLine(status: status),
            SkyNowCard(status: status),
          ],
        ),
      ),
    );
    expect(find.text('gut eingemessen'), findsOneWidget);
    expect(find.text('Abweichung 0,08°'), findsOneWidget);
    expect(find.text('Norden bei 78°'), findsOneWidget);
    expect(find.text('412'), findsOneWidget);
    expect(find.text('8 %'), findsOneWidget);
    expect(find.text('20,6'), findsOneWidget);
    expect(find.byKey(const Key('cloud-map')), findsOneWidget);

    await tester.pumpWidget(
      _host(
        const Column(
          children: [
            OrientationLine(status: {}),
            SkyNowCard(status: {}),
          ],
        ),
      ),
    );
    expect(find.byKey(const Key('orientation-line')), findsNothing);
    expect(find.byKey(const Key('sky-now')), findsNothing);
  });

  test('image angle of an azimuth, mirrored or not (SPEC §6.3)', () {
    expect(imageAngle(90, 0, true), -90); // east left
    expect(imageAngle(90, 0, false), 90); // east right
    expect(imageAngle(0, 78, true), 78);
  });

  testWidgets('satellites now and tonight', (tester) async {
    final asked = <String>[];
    final hub = MockClient((request) async {
      asked.add(request.url.queryParameters['f'] ?? '');
      final f = request.url.queryParameters['f'];
      return http.Response(
        jsonEncode({
          'located': true,
          'loaded': true,
          'now': f == 'nacht'
              ? []
              : [
                  {
                    'norad': 25544,
                    'name': 'Raumstation ISS',
                    'altitude': 59.1,
                    'azimuth': 160.0,
                    'height_km': 420,
                    'sunlit': true,
                    'naked_eye': true,
                    'in_picture': true,
                  },
                ],
          'passes': f == 'nacht'
              ? [
                  {
                    'norad': 25544,
                    'name': 'Raumstation ISS',
                    'start': '2026-11-14T04:30:00+00:00',
                    'end': '2026-11-14T04:36:00+00:00',
                    'top_altitude': 59.0,
                    'top_azimuth': 160.0,
                    'start_azimuth': 240.0,
                    'end_azimuth': 90.0,
                  },
                ]
              : [],
        }),
        200,
      );
    });
    final client = HubClient(
      baseUrl: 'https://hub.example',
      token: 't',
      httpClient: hub,
    );
    await tester.pumpWidget(
      _host(SatellitesCard(client: client, cameraId: 'cam1')),
    );
    await tester.pumpAndSettle();
    expect(find.text('Raumstation ISS'), findsOneWidget);
    expect(find.text('mit bloßem Auge'), findsOneWidget);
    expect(find.text('im Bild'), findsOneWidget);
    await tester.tap(find.text('Diese Nacht'));
    await tester.pumpAndSettle();
    expect(asked, ['jetzt', 'nacht']);
    expect(find.textContaining('bis 59° S'), findsOneWidget);
  });
}
