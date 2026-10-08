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
  final labels = <String?>[];

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
      if (path.endsWith('/label')) {
        final label =
            (jsonDecode(request.body) as Map<String, dynamic>)['label'];
        labels.add(label as String?);
        return http.Response(
          jsonEncode({
            ...meteorJson('meteor-20261008T214512Z', '2026-10-08T21:45:12Z'),
            'label': label,
          }),
          200,
        );
      }
      if (path.endsWith('/image') &&
          request.url.queryParameters['variant'] == null) {
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
      'https://hub.example/api/v1/cameras/cam/events/20261008/meteor-20261008T214512Z/image?variant=thumb&v=1',
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

  testWidgets(
    'label: kein Meteor, then clear; list hides false positives by default',
    (tester) async {
      tester.view.physicalSize = const Size(400, 2000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final hub = FakeEventHub(fullAfter: 0);
      final client = hub.client;
      await tester.pumpWidget(
        MaterialApp(
          home: EventsScreen(
            client: client,
            camera: Camera.fromJson(cameraJson),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(hub.pages.first['hide_false'], 'true');
      await tester.tap(find.byKey(const Key('show-all')));
      await tester.pumpAndSettle();
      expect(hub.pages.last.containsKey('hide_false'), isFalse);

      await tester.tap(find.text('Meteor'));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('false-positive')));
      await tester.pumpAndSettle();
      expect(find.text('Als „kein Meteor“ markiert.'), findsOneWidget);
      await tester.tap(find.byKey(const Key('clear-label')));
      await tester.pumpAndSettle();
      expect(hub.labels, ['false_positive', null]);
      expect(find.byKey(const Key('confirm')), findsOneWidget);
    },
  );

  Map<String, Object?> flashJson(
    int minute,
    double area, {
    String storm = 'storm-20261008T213012Z',
  }) => {
    'id': 'lightning-20261008T21${30 + minute}12Z',
    'night_id': '20261008',
    'kind': 'lightning',
    'start': '2026-10-08T21:${30 + minute}:12Z',
    'end': '2026-10-08T21:${30 + minute}:12Z',
    'confidence': 0.9,
    'has_image': true,
    'has_thumb': true,
    'has_full': false,
    'data': {
      'area_frac': area,
      'peak': 0.4,
      'storm_flashes': minute + 1,
      'storm': storm,
    },
  };

  test('flashes of one storm become one group; other events stay', () {
    final events = [
      SkyEvent.fromJson(flashJson(20, 0.1)),
      SkyEvent.fromJson(
        meteorJson('meteor-20261008T214512Z', '2026-10-08T21:45:12Z'),
      ),
      SkyEvent.fromJson(flashJson(10, 0.6)),
      SkyEvent.fromJson(flashJson(0, 0.3)),
      SkyEvent.fromJson(flashJson(5, 0.2, storm: 'kaputt')),
    ];
    final items = groupStorms(events);
    expect(items.length, 3);
    final storm = items.first as StormGroup;
    expect(storm.flashes.length, 3);
    expect(storm.cover.data['area_frac'], 0.6);
    expect(storm.start, DateTime.utc(2026, 10, 8, 21, 30, 12));
    expect(items.last, isA<SkyEvent>()); // a malformed key is not grouped
    final rows = {for (final (l, v) in eventRows(events.first)) l: v};
    expect(rows['Erhellter Himmel'], '10 %');
    expect(rows.containsKey('storm'), isFalse);
  });

  testWidgets('a storm is one list entry and opens its flashes', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(400, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final client = HubClient(
      baseUrl: 'https://hub.example',
      token: 'tok',
      httpClient: MockClient((request) async {
        if (request.url.path.endsWith('/events')) {
          final first = request.url.queryParameters['before'] == null;
          return http.Response(
            jsonEncode(
              first
                  ? [flashJson(20, 0.1), flashJson(10, 0.6), flashJson(0, 0.3)]
                  : [],
            ),
            200,
          );
        }
        return http.Response('', 404);
      }),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: EventsScreen(client: client, camera: Camera.fromJson(cameraJson)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Gewitter'), findsOneWidget);
    expect(find.textContaining('3 Blitze'), findsOneWidget);
    await tester.tap(find.text('Gewitter'));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('lightning-20261008T213012Z')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('lightning-20261008T215012Z')),
      findsOneWidget,
    );
  });

  test('aurora: German rows, image revision in the URL', () {
    final aurora = SkyEvent.fromJson({
      'id': 'aurora-20261008T220000Z',
      'night_id': '20261008',
      'kind': 'aurora',
      'start': '2026-10-08T22:00:00Z',
      'end': '2026-10-08T22:12:00Z',
      'confidence': 0.7,
      'has_image': true,
      'has_thumb': true,
      'has_full': false,
      'image_rev': 3,
      'data': {
        'peak_index': 30,
        'green': 1.8,
        'frames': 14,
        'direction_deg': 40.0,
        'ongoing': true,
        'image_rev': 3,
      },
    });
    final rows = {for (final (l, v) in eventRows(aurora)) l: v};
    expect(rows['Stärke'], '30 %');
    expect(rows['Bildrichtung'], '40°');
    expect(rows['Status'], 'läuft noch');
    expect(rows.containsKey('image_rev'), isFalse);
    expect(aurora.ongoing, isTrue);
    final client = HubClient(baseUrl: 'https://hub.example');
    expect(client.eventImageUrl('cam', aurora).query, 'v=3');
  });
}
