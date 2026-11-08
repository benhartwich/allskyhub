// Fireballs sent later in the app (late_fireball_screens.dart, SPEC §10.8): the list, the
// recording and its time, the track, the check.

import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/late_fireball_screens.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

http.Response _json(Object body, [int status = 200]) =>
    http.Response.bytes(utf8.encode(jsonEncode(body)), status);

// A PNG of 1 × 1 pixel: the camera of these tests sends frames of that size.
final _png = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
);

const _device = '01STATION0000000000000000';

Map<String, Object?> _item(
  String state, {
  String? reason,
  List<List<double>> track = const [],
  List<Map<String, Object?>> pairs = const [],
  String? moon,
}) => {
  'id': 'f1',
  'state': state,
  'reason': reason,
  'reason_text': reason == null
      ? null
      : 'Die Sterne passen nicht zu Ort und Zeit. Stimmen Datum, Uhrzeit und Zeitzone?',
  'taken_at': '2026-11-03T20:45:12Z',
  'local': '2026-11-03T21:45:12',
  'exposure_s': 30.0,
  'width': 1,
  'height': 1,
  'track': track,
  'stars': state == 'accepted' ? 41 : null,
  'offset_deg': state == 'accepted' ? 0.21 : null,
  'peak': state == 'accepted' ? 0.95 : null,
  'moon': moon,
  'orientation_until': null,
  'hidden': false,
  'pairs': pairs,
  'created_at': '2026-11-08T12:00:00Z',
};

class _Hub {
  _Hub({
    this.sizes = const [
      [1, 1],
    ],
    this.after = 'accepted',
  });

  final List<List<int>> sizes;

  /// What the check comes to.
  final String after;
  final sent = <http.Request>[];
  var state = 'track';
  var track = <List<double>>[];

  HubClient client() => HubClient(
    baseUrl: 'https://hub.example',
    token: 't',
    httpClient: MockClient((request) async {
      sent.add(request);
      final path = request.url.path;
      final base = '/api/v1/devices/$_device/late';
      if (path == base && request.method == 'GET') {
        return _json({
          'sizes': sizes,
          'zone': 'Europe/Vienna',
          'per_day': 20,
          'uploads': [
            if (state != 'track' || track.isNotEmpty)
              _item(state, track: track),
          ],
        });
      }
      if (path == base && request.method == 'POST') {
        return _json(_item('track'), 201);
      }
      if (path == '$base/f1/track') {
        track = [
          for (final p
              in (jsonDecode(request.body) as Map<String, dynamic>)['track']
                  as List<dynamic>)
            [for (final v in p as List<dynamic>) (v as num).toDouble()],
        ];
        state = 'checking';
        return _json(_item('checking', track: track));
      }
      if (path == '$base/f1/time') {
        state = 'checking';
        return _json(_item('checking', track: track));
      }
      if (path == '$base/f1' && request.method == 'GET') {
        if (state == 'checking') state = after;
        return _json(
          _item(
            state,
            track: track,
            reason: state == 'failed' ? 'no_match' : null,
            moon: state == 'accepted' ? 'confirmed' : null,
            pairs: state == 'accepted'
                ? [
                    {
                      'id': 'p1',
                      'other': 'Salzburg',
                      'high_km': 95.2,
                      'low_km': 49.8,
                    },
                  ]
                : const [],
          ),
        );
      }
      if (path == '$base/f1' && request.method == 'DELETE') {
        return http.Response('', 204);
      }
      return http.Response('not found', 404);
    }),
  );
}

void main() {
  test('the time from an Allsky file name, as the hub takes it', () {
    final t = timeFromName('image-20261103214512.jpg');
    expect(t, DateTime(2026, 11, 3, 21, 45, 12));
    expect(wallClock(t!), '2026-11-03T21:45:12');
    expect(timeFromName('image.jpg'), isNull);
    expect(timeFromName('image-20261399214512.jpg'), isNull);
  });

  testWidgets('a camera without an orientation cannot send any yet', (
    tester,
  ) async {
    final hub = _Hub(sizes: const []);
    await tester.pumpWidget(
      MaterialApp(
        home: LateFireballsScreen(
          client: hub.client(),
          deviceId: _device,
          name: 'Linz',
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.textContaining('nicht an den Sternen ausgerichtet'),
      findsOneWidget,
    );
    expect(find.byKey(const Key('late-new')), findsNothing);
  });

  testWidgets('send, mark the track, checked and measured', (tester) async {
    final hub = _Hub();
    final client = hub.client();
    final list = await client.lateFireballs(_device);
    await tester.pumpWidget(
      MaterialApp(
        home: LateSendScreen(
          client: client,
          deviceId: _device,
          list: list,
          pick: () async => (_png.toList(), 'image-20261103214512.png'),
        ),
      ),
    );
    await tester.runAsync(() async {
      await tester.tap(find.byKey(const Key('late-pick')));
      await Future<void>.delayed(const Duration(milliseconds: 200));
    });
    await tester.pump();
    expect(find.textContaining('passt zur Kamera'), findsOneWidget);
    // The time came from the file name.
    expect(
      tester
          .widget<TextField>(find.byKey(const Key('late-time')))
          .controller!
          .text,
      '2026-11-03 21:45:12',
    );
    await tester.tap(find.byKey(const Key('late-send')));
    await tester.pumpAndSettle();
    final upload = hub.sent.lastWhere((r) => r.method == 'POST');
    expect(latin1.decode(upload.bodyBytes), contains('2026-11-03T21:45:12'));
    expect(latin1.decode(upload.bodyBytes), contains('Europe/Vienna'));

    // The track screen: two taps, then the check.
    expect(find.byKey(const Key('late-frame')), findsOneWidget);
    final check = find.byKey(const Key('late-check'));
    expect(tester.widget<FilledButton>(check).onPressed, isNull);
    final frame = tester.getRect(find.byKey(const Key('late-frame')));
    await tester.tapAt(
      frame.topLeft + Offset(frame.width * 0.2, frame.height * 0.3),
    );
    await tester.tapAt(
      frame.topLeft + Offset(frame.width * 0.8, frame.height * 0.6),
    );
    await tester.pump();
    expect(tester.widget<FilledButton>(check).onPressed, isNotNull);
    await tester.tap(check);
    await tester.pumpAndSettle();
    expect(hub.track, hasLength(2));
    expect(hub.track[0][0], closeTo(0.2, 0.05));
    expect(hub.track[1][1], closeTo(0.6, 0.05));

    expect(find.text('angenommen'), findsOneWidget);
    expect(find.textContaining('Auch das Datum stimmt'), findsOneWidget);
    expect(find.text('Bahn mit Salzburg'), findsOneWidget);
    expect(find.text('95 → 50 km'), findsOneWidget);
  });

  testWidgets('a failed one says why and is checked again with another time', (
    tester,
  ) async {
    final hub = _Hub(after: 'failed')
      ..state = 'failed'
      ..track = [
        [0.1, 0.1],
        [0.9, 0.9],
      ];
    await tester.pumpWidget(
      MaterialApp(
        home: LateFireballScreen(
          client: hub.client(),
          deviceId: _device,
          id: 'f1',
          every: const Duration(milliseconds: 10),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('nicht bestanden'), findsOneWidget);
    expect(
      find.textContaining('Stimmen Datum, Uhrzeit und Zeitzone'),
      findsOneWidget,
    );
    await tester.enterText(
      find.byKey(const Key('late-retime')),
      '2026-11-03 21:47:12',
    );
    await tester.tap(find.byKey(const Key('late-retime-send')));
    await tester.pumpAndSettle();
    final again = hub.sent.lastWhere((r) => r.url.path.endsWith('/time'));
    expect(jsonDecode(again.body), {
      'taken': '2026-11-03T21:47:12',
      'zone': 'Europe/Vienna',
      'exposure_s': 30.0,
    });

    await tester.tap(find.byKey(const Key('late-delete')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('late-delete-ok')));
    await tester.pumpAndSettle();
    expect(hub.sent.last.method, 'DELETE');
  });
}
