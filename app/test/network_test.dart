// SharedSky for everyone who takes part (network_screens.dart): "Das Netz" from
// "Entdecken", and on the own station "Im Netz", "Dein Himmel im Vergleich" and the four
// choices of what it shows (SPEC §10.6).

import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/explore_screen.dart';
import 'package:allskyhub_app/screens/network_screens.dart';
import 'package:allskyhub_app/screens/station_screens.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

http.Response _json(Object body, [int status = 200]) =>
    http.Response.bytes(utf8.encode(jsonEncode(body)), status);

const _network = {
  'cameras': 1,
  'stations': 2,
  'active': 2,
  'meteors_tonight': 212,
  'fireballs_month': 4,
  'showers_tonight': [
    {'name': 'Leoniden', 'count': 38},
  ],
  'tonight': [
    {
      'id': 'f1',
      'at': '2026-11-07T21:41:00Z',
      'high_km': 92.0,
      'low_km': 41.0,
      'cameras': ['Lanzenberg', 'Sternwarte Musterberg'],
    },
  ],
  'darkest': [
    {
      'name': 'Sternwarte Musterberg',
      'slug': 'k3v',
      'kind': 'station',
      'sqm': 21.48,
    },
  ],
  'members': [
    {
      'name': 'Sternwarte Musterberg',
      'slug': 'k3v',
      'kind': 'station',
      'software': 'indi-allsky',
      'since': '2026-11-05T10:00:00Z',
      'active': true,
      'meteors': 61,
      'fireballs': 2,
    },
    {
      'name': 'eine Station im Netz',
      'slug': null,
      'kind': 'station',
      'software': 'image-url',
      'since': null,
      'active': false,
      'meteors': 3,
      'fireballs': 0,
    },
  ],
  'points': [
    {
      'lat': 49.9,
      'lon': 10.9,
      'title': 'Sternwarte Musterberg',
      'slug': 'k3v',
      'kind': 'station',
      'active': true,
    },
  ],
};

List<Map<String, Object?>> _nights() => [
  for (var k = 0; k < 30; k++)
    {
      'night_id': '202611${(1 + k % 28).toString().padLeft(2, '0')}',
      'own': k % 4 == 0 ? null : 21.4,
      'low': k % 4 == 0 ? null : 20.9,
      'high': k % 4 == 0 ? null : 21.1,
    },
];

Map<String, Object?> _station({
  Map<String, Object> visibility = const {
    'named': true,
    'on_map': true,
    'page': true,
    'images': false,
  },
  bool waiting = false,
}) => {
  'id': '01STATION0000000000000000',
  'name': 'Sternwarte Musterberg',
  'software': 'indi-allsky',
  'state': 'connected',
  'frames_today': 412,
  'meteors_today': 7,
  'visibility': visibility,
  'public_url': visibility['page'] == true
      ? 'https://hub.example/sky/k3v'
      : null,
  'public_from': null,
  'public_waiting': waiting,
};

class _Hub {
  final sent = <http.Request>[];

  HubClient client() => HubClient(
    baseUrl: 'https://hub.example',
    token: 't',
    httpClient: MockClient((request) async {
      sent.add(request);
      final path = request.url.path;
      if (path == '/api/v1/public/network') return _json(_network);
      if (path == '/api/v1/public/cameras') return _json(<Object>[]);
      if (path == '/api/v1/public/feed') return _json({'events': <Object>[]});
      if (path.endsWith('/visibility') && request.method == 'PUT') {
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        return _json(_station(visibility: body.cast<String, Object>()));
      }
      if (path == '/api/v1/devices/01STATION0000000000000000/network') {
        return _json({
          'since': '2026-11-05T10:00:00Z',
          'fireballs': 2,
          'meteors': 61,
          'recent': _network['tonight'],
          'comparison': {
            'nights': _nights(),
            'neighbours': 3,
            'difference': 0.4,
          },
        });
      }
      return http.Response('', 404);
    }),
  );
}

void _tall(WidgetTester tester) {
  tester.view.physicalSize = const Size(800, 3000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

void main() {
  testWidgets('"Entdecken" leads to the network', (tester) async {
    _tall(tester);
    await tester.pumpWidget(
      MaterialApp(home: ExploreScreen(client: _Hub().client())),
    );
    await tester.pumpAndSettle();
    expect(find.text('Das Netz heute Nacht'), findsOneWidget);
    expect(find.textContaining('212 Meteore'), findsOneWidget);

    await tester.tap(find.byKey(const Key('network-card')));
    await tester.pumpAndSettle();
    expect(find.text('Das Netz'), findsOneWidget);
    expect(find.textContaining('3 Kameras messen mit'), findsOneWidget);
    expect(find.text('Leoniden'), findsOneWidget);
    expect(find.text('21,48'), findsWidgets);
    expect(
      find.byKey(const Key('member-Sternwarte Musterberg')),
      findsOneWidget,
    );
    expect(
      find.byKey(const Key('member-eine Station im Netz')),
      findsOneWidget,
    );
    expect(find.text('indi-allsky'), findsOneWidget);
    expect(find.text('Allsky'), findsOneWidget);
  });

  testWidgets('an older hub without the network shows no card', (tester) async {
    _tall(tester);
    final client = HubClient(
      baseUrl: 'https://hub.example',
      httpClient: MockClient(
        (request) async => switch (request.url.path) {
          '/api/v1/public/cameras' => _json(<Object>[]),
          _ => http.Response('', 404),
        },
      ),
    );
    await tester.pumpWidget(MaterialApp(home: ExploreScreen(client: client)));
    await tester.pumpAndSettle();
    expect(find.byType(NetworkCard), findsNothing);
  });

  testWidgets('the own station: its part, the comparison and the choices', (
    tester,
  ) async {
    _tall(tester);
    final hub = _Hub();
    await tester.pumpWidget(
      MaterialApp(
        home: StationScreen(
          client: hub.client(),
          station: Station.fromJson(_station()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Im Netz'), findsOneWidget);
    expect(find.textContaining('2 Feuerkugeln mitvermessen'), findsOneWidget);
    expect(find.byKey(const Key('sky-comparison')), findsOneWidget);
    expect(
      find.text('Im Mittel 0,4 mag/″² dunkler als deine Nachbarn.'),
      findsOneWidget,
    );
    expect(find.text('https://hub.example/sky/k3v'), findsOneWidget);

    // Switching the page off is saved at once; its address goes.
    await tester.tap(find.byKey(const Key('visibility-page')));
    await tester.pumpAndSettle();
    final put = hub.sent.lastWhere((r) => r.method == 'PUT');
    expect(jsonDecode(put.body), {
      'named': true,
      'on_map': true,
      'page': false,
      'images': false,
    });
    expect(find.text('https://hub.example/sky/k3v'), findsNothing);
  });

  testWidgets('a station that joined before is told about its notice', (
    tester,
  ) async {
    _tall(tester);
    await tester.pumpWidget(
      MaterialApp(
        home: StationScreen(
          client: _Hub().client(),
          station: Station.fromJson(_station(waiting: true)),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.textContaining('eine Woche nach unserer Mail an dich'),
      findsOneWidget,
    );
  });
}
