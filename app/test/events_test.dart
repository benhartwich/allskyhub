import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/event_screens.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'hub_client_test.dart' show cameraJson;

Map<String, Object?> meteorJson(String id, String start) => {
  'id': id,
  'night_id': '20261008',
  'kind': 'meteor',
  'start': start,
  'end': start.replaceFirst('12Z', '13Z'),
  'confidence': 0.87,
  'has_image': true,
  'has_thumb': true,
  'has_full': false,
  'data': {
    'length_px': 412,
    'peak': 0.93,
    'frames': 3,
    'direction_deg': 135.0,
    'shower': 'Orioniden',
    'extra': 7,
  },
};

class FakeEventHub {
  FakeEventHub({this.fullAfter = 1});

  final int fullAfter;
  int fullRequests = 0;
  final pages = <Map<String, String>>[];

  HubClient get client => HubClient(
    baseUrl: 'https://hub.example',
    token: 'tok',
    httpClient: MockClient((request) async {
      final path = request.url.path;
      if (path.endsWith('/events') && !path.contains('/nights/')) {
        pages.add(request.url.queryParameters);
        final first = request.url.queryParameters['before'] == null;
        return http.Response(
          jsonEncode(
            first
                ? [
                    meteorJson(
                      'meteor-20261008T214512Z',
                      '2026-10-08T21:45:12Z',
                    ),
                  ]
                : <Object>[],
          ),
          200,
        );
      }
      if (path.endsWith('/image') && request.url.queryParameters.isEmpty) {
        fullRequests++;
        return fullRequests > fullAfter
            ? http.Response('jpeg', 200)
            : http.Response('{"status": "requested"}', 202);
      }
      return http.Response('', 404);
    }),
  );
}

void main() {
  test('meteor data in German, other keys raw', () {
    final event = SkyEvent.fromJson(
      meteorJson('meteor-20261008T214512Z', '2026-10-08T21:45:12Z'),
    );
    final rows = {for (final (label, value) in eventRows(event)) label: value};
    expect(rows['Sicherheit'], '87 %');
    expect(rows['Dauer'], '1.0 s');
    expect(rows['Spurlänge'], '412 px');
    expect(rows['Helligkeit'], '93 %');
    expect(rows['Bilder'], '3');
    expect(rows['Richtung'], '135° (diagonal, oben links – unten rechts)');
    expect(rows['Meteorstrom'], 'Orioniden');
    expect(rows['extra'], '7');
  });

  test('client pages events with before', () async {
    final hub = FakeEventHub();
    final client = hub.client;
    final first = await client.events('cam', limit: 30);
    expect(first.single.kind, 'meteor');
    final more = await client.events(
      'cam',
      before: first.last.start,
      limit: 30,
    );
    expect(more, isEmpty);
    expect(hub.pages.last['before'], '2026-10-08T21:45:12.000Z');
    expect(
      client.eventImageUrl('cam', first.single, thumb: true).toString(),
      'https://hub.example/api/v1/cameras/cam/events/20261008/meteor-20261008T214512Z/image?variant=thumb',
    );
  });

  testWidgets('events list → detail fetches the full picture', (tester) async {
    tester.view.physicalSize = const Size(400, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final hub = FakeEventHub(fullAfter: 1);
    final client = hub.client;
    await tester.pumpWidget(
      MaterialApp(
        home: EventsScreen(client: client, camera: Camera.fromJson(cameraJson)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Meteor'), findsOneWidget);

    await tester.tap(find.text('Meteor'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(
      find.textContaining('Vollbild wird von der Kamera geholt'),
      findsOneWidget,
    );
    expect(find.text('Orioniden'), findsOneWidget);
    await tester.pump(const Duration(seconds: 4));
    await tester.pump();
    expect(hub.fullRequests, 2);
    expect(
      find.textContaining('Vollbild wird von der Kamera geholt'),
      findsNothing,
    );
  });

  testWidgets('no events: a clear message', (tester) async {
    final client = HubClient(
      baseUrl: 'https://hub.example',
      token: 'tok',
      httpClient: MockClient((_) async => http.Response('[]', 200)),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: EventsScreen(client: client, camera: Camera.fromJson(cameraJson)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('Noch keine Ereignisse'), findsOneWidget);
  });
}
