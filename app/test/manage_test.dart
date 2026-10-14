import 'dart:convert';
import 'dart:typed_data';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/manage_screens.dart';
import 'package:allskyhub_app/screens/stats_screen.dart';
import 'package:allskyhub_app/screens/target_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

final camera = Camera(id: 'cam1', name: 'Garten', profile: 'sim', online: true);

/// Roadmap C10, #9 and C7 against a small fake of the app API (api/app_manage.py).
class FakeManageHub {
  var discord = <String, dynamic>{};
  var discordTests = 0;
  var network = false;
  final members = <Map<String, dynamic>>[];
  var enabled = false;
  var onHome = false;
  var profile = <String, dynamic>{
    'title': '',
    'location_label': '',
    'show_coordinates': false,
    'camera': '',
    'lens': '',
    'computer': '',
    'owner': '',
    'notes': '',
    'sections': {'live': true, 'loop': true, 'observatory': false},
    'future_field': 'kept',
  };

  HubClient get client => HubClient(
    baseUrl: 'https://hub.example',
    token: 'tok',
    httpClient: MockClient((request) async {
      final path = request.url.path;
      if (path == '/api/v1/cameras/cam1/members') {
        if (request.method == 'POST') {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          if (body['email'] == 'nobody@example.org') {
            return _json({
              'detail': 'Es gibt kein Konto mit dieser Adresse.',
            }, 400);
          }
          members.add({
            'user_id': 'u${members.length + 1}',
            'email': body['email'],
            'role': body['role'],
          });
        }
        return _json(members);
      }
      if (path.startsWith('/api/v1/cameras/cam1/members/') &&
          request.method == 'DELETE') {
        members.removeWhere((m) => path.endsWith('/${m['user_id']}'));
        return http.Response('', 204);
      }
      if (path == '/api/v1/cameras/cam1/public/profile') {
        if (request.method == 'PUT') {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          if (body['enabled'] is bool) enabled = body['enabled'] as bool;
          if (body['profile'] case final Map<String, dynamic> p) profile = p;
          if (body['on_home'] is bool) onHome = body['on_home'] as bool;
        }
        return _json({
          'enabled': enabled,
          'url': enabled ? 'https://hub.example/sky/abc' : null,
          'profile': profile,
          'on_home': onHome,
        });
      }
      if (path == '/api/v1/cameras/cam1/stats') {
        final year = int.parse(request.url.queryParameters['year'] ?? '2026');
        Map<String, dynamic> period(String label, int nights) => {
          'label': label,
          'nights': nights,
          'clear_nights': nights ~/ 2,
          'clear_hours': nights * 3.0,
          'best_sqm': nights > 0 ? 21.3 : null,
          'meteors': nights * 2,
          'aurora': 0,
          'storms': 0,
        };
        final full = year == 2026;
        return _json({
          'year': year,
          'months': [
            for (var i = 1; i <= 12; i++)
              period('M$i', full && i == 10 ? 4 : 0),
          ],
          'total': period('$year', full ? 4 : 0),
          'showers': full ? {'Orioniden': 3} : <String, int>{},
          'years': [2026, 2025],
        });
      }
      if (path == '/api/v1/cameras/cam1/exports') {
        return _json([
          {
            'name': 'sqm',
            'title': 'Himmelshelligkeit pro Nacht',
            'filename': 'sqm.csv',
          },
        ]);
      }
      if (path == '/api/v1/cameras/cam1/exports/sqm.csv') {
        return http.Response('night,sqm\n20261011,21.3\n', 200);
      }
      if (path.startsWith('/api/v1/cameras/cam1/discord')) {
        if (path.endsWith('/test')) {
          discordTests++;
          return http.Response('', 204);
        }
        if (request.method == 'PUT') {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          if (body['webhook'] == 'https://example.org') {
            return _json({
              'detail': 'Das ist keine Discord-Webhook-Adresse.',
            }, 400);
          }
          discord = {
            'webhook': (body['webhook'] as String).isEmpty
                ? discord['webhook']
                : 'https://discord.com/api/webhooks/1/…',
            'kinds': body['kinds'],
            'summary': body['summary'],
          };
        }
        if (request.method == 'DELETE') discord = {};
        return _json({
          'configured': discord.isNotEmpty,
          'webhook': discord['webhook'],
          'kinds': discord['kinds'] ?? ['meteor', 'aurora', 'nlc'],
          'summary': discord['summary'] ?? true,
          'broken': false,
          'available': {
            'meteor': 'Meteore',
            'aurora': 'Polarlicht',
            'nlc': 'NLC',
          },
        });
      }
      if (path == '/api/v1/cameras/cam1/network') {
        if (request.method == 'PUT') {
          network = (jsonDecode(request.body) as Map)['network'] as bool;
        }
        return _json({'network': network});
      }
      if (path == '/api/v1/targets') {
        return _json([
          {'key': 'jupiter', 'label': 'Jupiter', 'kind': 'planet', 'mag': null},
          {
            'key': 'M31',
            'label': 'M31 Andromedagalaxie',
            'kind': 'gx',
            'mag': 3.4,
          },
        ]);
      }
      if (path == '/api/v1/cameras/cam1/target') {
        final key = request.url.queryParameters['key'];
        if (key == 'jupiter') {
          return _json({
            'detail': 'Die Kamera kennt ihren Standort noch nicht.',
          }, 409);
        }
        Map<String, dynamic> hour(int h, String v, String text) => {
          'at': '2026-10-15T${h.toString().padLeft(2, '0')}:00:00Z',
          'alt': 50.0,
          'az': 90.0,
          'direction': 'O',
          'clouds': 10,
          'moon_near': h == 21,
          'verdict': v,
          'verdict_text': text,
        };
        return _json({
          'key': 'M31',
          'label': 'M31 Andromedagalaxie',
          'summary': 'M31 Andromedagalaxie ist jetzt frei.',
          'tz': 'UTC',
          'alt': 62.4,
          'az': 80.0,
          'direction': 'O',
          'cloud_cover': 0.05,
          'cloud_source': 'in Richtung des Objekts',
          'verdict': 'clear',
          'verdict_text': 'frei',
          'best_start': null,
          'best_end': null,
          'hours': [hour(21, 'clear', 'frei'), hour(22, 'cloudy', 'bewölkt')],
        });
      }
      return http.Response('', 404);
    }),
  );
}

http.Response _json(Object body, [int status = 200]) => http.Response(
  jsonEncode(body),
  status,
  headers: {'content-type': 'application/json; charset=utf-8'},
);

Future<void> _pump(WidgetTester tester, Widget screen) async {
  tester.view.physicalSize = const Size(420, 2400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(home: screen));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('shares and stops sharing', (tester) async {
    final hub = FakeManageHub();
    await _pump(tester, SharingScreen(client: hub.client, camera: camera));
    expect(find.text('Noch niemand.'), findsOneWidget);

    await tester.enterText(
      find.byKey(const Key('share-email')),
      'nobody@example.org',
    );
    await tester.tap(find.byKey(const Key('share-add')));
    await tester.pumpAndSettle();
    expect(find.text('Es gibt kein Konto mit dieser Adresse.'), findsOneWidget);

    await tester.enterText(
      find.byKey(const Key('share-email')),
      'oma@example.org',
    );
    await tester.tap(find.text('Verwalten'));
    await tester.tap(find.byKey(const Key('share-add')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('share-error')), findsNothing);
    expect(find.text('oma@example.org'), findsOneWidget);
    expect(hub.members.single['role'], 'admin');

    await tester.tap(find.byTooltip('Nicht mehr teilen'));
    await tester.pumpAndSettle();
    expect(hub.members, isEmpty);
    expect(find.text('Noch niemand.'), findsOneWidget);
  });

  testWidgets('public page: switch on, edit, keep unknown fields', (
    tester,
  ) async {
    final hub = FakeManageHub();
    await _pump(tester, PublicPageScreen(client: hub.client, camera: camera));
    expect(find.byKey(const Key('public-url')), findsNothing);

    await tester.tap(find.byKey(const Key('public-enabled')));
    await tester.pumpAndSettle();
    expect(hub.enabled, isTrue);
    expect(find.text('https://hub.example/sky/abc'), findsOneWidget);

    await tester.enterText(find.byKey(const Key('public-title')), 'Sternwarte');
    await tester.tap(find.byKey(const Key('section-observatory')));
    await tester.tap(find.byKey(const Key('show-coordinates')));
    await tester.tap(find.byKey(const Key('on-home')));
    await tester.tap(find.byKey(const Key('public-save')));
    await tester.pumpAndSettle();
    expect(find.text('Gespeichert.'), findsOneWidget);
    expect(hub.profile['title'], 'Sternwarte');
    expect(hub.profile['show_coordinates'], isTrue);
    expect(hub.onHome, isTrue);
    expect(
      hub.profile.containsKey('on_home'),
      isFalse,
    ); // not part of the profile
    expect((hub.profile['sections'] as Map)['observatory'], isTrue);
    expect((hub.profile['sections'] as Map)['live'], isTrue);
    expect(hub.profile['future_field'], 'kept');
  });

  testWidgets('statistics per year', (tester) async {
    final hub = FakeManageHub();
    await _pump(tester, StatsScreen(client: hub.client, camera: camera));
    expect(find.text('2 von 4'), findsOneWidget);
    expect(find.text('Orioniden'), findsOneWidget);
    expect(find.text('M10'), findsNWidgets(2)); // bar label and table row

    await tester.tap(find.text('2025'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('stats-empty')), findsOneWidget);
  });

  testWidgets('exports are handed to the share sheet', (tester) async {
    final hub = FakeManageHub();
    final shared = <String, String>{};
    await _pump(
      tester,
      ExportScreen(
        client: hub.client,
        camera: camera,
        share: (Uint8List bytes, String name, Rect? origin) async {
          shared[name] = utf8.decode(bytes);
        },
      ),
    );
    await tester.tap(find.byKey(const Key('export-sqm')));
    await tester.pumpAndSettle();
    expect(shared, {'allskyhub-cam1-sqm.csv': 'night,sqm\n20261011,21.3\n'});
  });

  testWidgets('discord: wrong address, save, test, remove', (tester) async {
    final hub = FakeManageHub();
    await _pump(tester, DiscordScreen(client: hub.client, camera: camera));
    expect(find.byKey(const Key('discord-test')), findsNothing);

    await tester.enterText(
      find.byKey(const Key('discord-webhook')),
      'https://example.org',
    );
    await tester.tap(find.byKey(const Key('discord-save')));
    await tester.pumpAndSettle();
    expect(find.text('Das ist keine Discord-Webhook-Adresse.'), findsOneWidget);

    await tester.enterText(
      find.byKey(const Key('discord-webhook')),
      'https://discord.com/api/webhooks/1/secret',
    );
    await tester.tap(find.byKey(const Key('discord-aurora')));
    await tester.tap(find.byKey(const Key('discord-save')));
    await tester.pumpAndSettle();
    expect(hub.discord['kinds'], ['meteor', 'nlc']);
    expect(find.textContaining('leer lassen = behalten'), findsOneWidget);

    await tester.tap(find.byKey(const Key('discord-test')));
    await tester.pumpAndSettle();
    expect(hub.discordTests, 1);
    await tester.tap(find.byKey(const Key('discord-remove')));
    await tester.pumpAndSettle();
    expect(hub.discord, isEmpty);
    expect(find.byKey(const Key('discord-test')), findsNothing);
  });

  testWidgets('target: choose an object, see now and the hours', (
    tester,
  ) async {
    final hub = FakeManageHub();
    await _pump(tester, TargetScreen(client: hub.client, camera: camera));
    await tester.tap(find.byKey(const Key('target-choice')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('M31 Andromedagalaxie').last);
    await tester.pumpAndSettle();
    expect(find.text('M31 Andromedagalaxie ist jetzt frei.'), findsOneWidget);
    expect(find.textContaining('Jetzt: 62° hoch im O'), findsOneWidget);
    expect(
      find.textContaining('5 % Wolken (in Richtung des Objekts)'),
      findsOneWidget,
    );
    expect(find.text('frei · Mond nah'), findsOneWidget);
    expect(find.text('bewölkt'), findsOneWidget);

    await tester.tap(find.byKey(const Key('target-choice')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Jupiter').last);
    await tester.pumpAndSettle();
    expect(
      find.text('Die Kamera kennt ihren Standort noch nicht.'),
      findsOneWidget,
    );
  });

  test('camera role defaults to owner for older hubs', () {
    final old = Camera.fromJson({
      'id': 'a',
      'name': 'n',
      'profile': 'p',
      'online': false,
    });
    expect(old.isOwner, isTrue);
    final shared = Camera.fromJson({
      'id': 'a',
      'name': 'n',
      'profile': 'p',
      'online': false,
      'role': 'viewer',
    });
    expect(shared.isOwner, isFalse);
    expect(shared.canChange, isFalse);
  });

  testWidgets('network: the owner joins the camera network', (tester) async {
    final hub = FakeManageHub();
    await _pump(tester, NetworkScreen(client: hub.client, camera: camera));
    expect(
      tester.widget<SwitchListTile>(find.byType(SwitchListTile)).value,
      false,
    );
    await tester.tap(find.byKey(const Key('network-switch')));
    await tester.pumpAndSettle();
    expect(hub.network, isTrue);
    expect(
      tester.widget<SwitchListTile>(find.byType(SwitchListTile)).value,
      true,
    );
  });
}
