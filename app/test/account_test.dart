import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/account_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class FakeAccountHub {
  final calls = <String, Map<String, dynamic>>{};
  int keepDays = 30;
  final notify = <String, bool>{
    'notify_events': false,
    'notify_offline': false,
    'notify_aurora': false,
    'notify_summary': false,
    'notify_tonight': false,
    'notify_by_mail': false,
  };
  bool push = false;
  bool mail = true;

  HubClient get client => HubClient(
    baseUrl: 'https://hub.example',
    token: 'tok',
    httpClient: MockClient((request) async {
      if (request.url.path == '/api/v1/account/settings') {
        if (request.method == 'PUT') {
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          keepDays = body['event_keep_days'] as int? ?? keepDays;
          for (final key in notify.keys.toList()) {
            if (body[key] is bool) notify[key] = body[key] as bool;
          }
        }
        return http.Response(
          jsonEncode({
            'event_keep_days': keepDays,
            ...notify,
            'push_available': push,
            'mail_available': mail,
          }),
          200,
        );
      }
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      calls[request.url.path] = body;
      if (request.url.path == '/api/v1/account/password') {
        return body['current'] == 'right password'
            ? http.Response('', 204)
            : http.Response(
                jsonEncode({'detail': 'Das aktuelle Passwort stimmt nicht.'}),
                400,
              );
      }
      if (request.url.path == '/api/v1/account/delete') {
        return body['password'] == 'right password'
            ? http.Response('', 204)
            : http.Response(
                jsonEncode({'detail': 'Das Passwort stimmt nicht.'}),
                400,
              );
      }
      return http.Response('', 404);
    }),
  );
}

Future<void> pumpAccount(
  WidgetTester tester,
  HubClient client,
  VoidCallback onDeleted,
) async {
  tester.view.physicalSize = const Size(400, 2400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    MaterialApp(
      home: AccountScreen(client: client, onDeleted: () async => onDeleted()),
    ),
  );
}

void main() {
  testWidgets(
    'change password: checks locally, shows the hub error, then succeeds',
    (tester) async {
      final hub = FakeAccountHub();
      await pumpAccount(tester, hub.client, () {});

      await tester.enterText(find.byKey(const Key('new')), 'new password 1');
      await tester.enterText(find.byKey(const Key('new2')), 'something else');
      await tester.tap(find.text('Passwort ändern').last);
      await tester.pump();
      expect(
        find.text('Die neuen Passwörter stimmen nicht überein.'),
        findsOneWidget,
      );
      expect(hub.calls, isEmpty);

      await tester.enterText(find.byKey(const Key('new2')), 'new password 1');
      await tester.enterText(find.byKey(const Key('current')), 'wrong');
      await tester.tap(find.text('Passwort ändern').last);
      await tester.pumpAndSettle();
      expect(find.text('Das aktuelle Passwort stimmt nicht.'), findsOneWidget);

      await tester.enterText(
        find.byKey(const Key('current')),
        'right password',
      );
      await tester.tap(find.text('Passwort ändern').last);
      await tester.pumpAndSettle();
      expect(find.textContaining('Passwort geändert'), findsOneWidget);
      expect(hub.calls['/api/v1/account/password'], {
        'current': 'right password',
        'new': 'new password 1',
      });
    },
  );

  testWidgets('delete account asks first and then signs out', (tester) async {
    final hub = FakeAccountHub();
    var deleted = false;
    final client = hub.client;
    await pumpAccount(tester, client, () => deleted = true);

    await tester.enterText(
      find.byKey(const Key('delete-password')),
      'right password',
    );
    await tester.tap(find.text('Konto löschen').last);
    await tester.pumpAndSettle();
    expect(find.text('Konto endgültig löschen?'), findsOneWidget);
    await tester.tap(find.text('Abbrechen'));
    await tester.pumpAndSettle();
    expect(hub.calls, isEmpty);

    await tester.tap(find.text('Konto löschen').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Löschen'));
    await tester.pumpAndSettle();
    expect(hub.calls['/api/v1/account/delete'], {'password': 'right password'});
    expect(deleted, isTrue);
    expect(client.token, isNull);
  });

  testWidgets('event retention: shows the current choice and saves a new one', (
    tester,
  ) async {
    final hub = FakeAccountHub();
    await pumpAccount(tester, hub.client, () {});
    await tester.pumpAndSettle();
    expect(find.text('Ereignisse aufbewahren'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('keep-365')));
    await tester.pumpAndSettle();
    expect(hub.keepDays, 365);
    expect(find.text('Gespeichert.'), findsOneWidget);
  });

  testWidgets('notifications by e-mail without push', (tester) async {
    final hub = FakeAccountHub();
    await pumpAccount(tester, hub.client, () {});
    await tester.pumpAndSettle();
    expect(find.text('Benachrichtigungen'), findsOneWidget);
    expect(find.text('Neue Ereignisse'), findsNothing); // push only
    await tester.tap(find.byKey(const ValueKey('notify_tonight')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('notify_by_mail')));
    await tester.pumpAndSettle();
    expect(hub.notify['notify_tonight'], isTrue);
    expect(hub.notify['notify_by_mail'], isTrue);
    expect(hub.notify['notify_events'], isFalse);
  });

  testWidgets('no notification section when the hub can send nothing', (
    tester,
  ) async {
    final hub = FakeAccountHub()..mail = false;
    await pumpAccount(tester, hub.client, () {});
    await tester.pumpAndSettle();
    expect(find.text('Benachrichtigungen'), findsNothing);
  });
}
