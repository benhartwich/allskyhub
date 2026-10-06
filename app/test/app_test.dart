import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/api/session_store.dart';
import 'package:allskyhub_app/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'hub_client_test.dart' show cameraJson;

MockClient fakeHub({List<Map<String, Object?>> cameras = const []}) =>
    MockClient(
      (request) async => switch (request.url.path) {
        '/api/v1/auth/login' =>
          jsonDecode(request.body)['password'] == 'right password'
              ? http.Response(jsonEncode({'access_token': 'tok'}), 200)
              : http.Response(
                  jsonEncode({'detail': 'Wrong email or password'}),
                  401,
                ),
        '/api/v1/cameras' => http.Response(jsonEncode(cameras), 200),
        _ => http.Response('', 404),
      },
    );

Widget app(SessionStore store, MockClient hub) => AllskyApp(
  store: store,
  clientFor: (url, token) =>
      HubClient(baseUrl: url, token: token, httpClient: hub),
);

void main() {
  testWidgets('signs in and shows the empty camera list', (tester) async {
    final store = MemorySessionStore();
    await tester.pumpWidget(app(store, fakeHub()));
    await tester.pumpAndSettle();

    await tester.enterText(find.byKey(const Key('email')), 'a@example.org');
    await tester.enterText(find.byKey(const Key('password')), 'wrong');
    await tester.tap(find.text('Anmelden'));
    await tester.pumpAndSettle();
    expect(
      find.text('E-Mail-Adresse oder Passwort ist falsch.'),
      findsOneWidget,
    );

    await tester.enterText(find.byKey(const Key('password')), 'right password');
    await tester.tap(find.text('Anmelden'));
    await tester.pumpAndSettle();
    expect(find.text('Meine Kameras'), findsOneWidget);
    expect(find.textContaining('Noch keine Kamera gekoppelt'), findsOneWidget);
    expect(store.session?.token, 'tok');
    expect(store.session?.hubUrl, defaultHubUrl);
  });

  testWidgets('restores the session and lists cameras', (tester) async {
    final store = MemorySessionStore()
      ..session = const StoredSession('https://hub.example', 'tok');
    await tester.pumpWidget(app(store, fakeHub(cameras: [cameraJson])));
    await tester.pumpAndSettle();
    expect(find.text('Garten'), findsOneWidget);
    expect(find.text('online'), findsOneWidget);
  });
}
