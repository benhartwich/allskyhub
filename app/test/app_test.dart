import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/api/session_store.dart';
import 'package:allskyhub_app/api/camera_setup.dart';
import 'package:allskyhub_app/main.dart';
import 'package:allskyhub_app/screens/onboarding_screen.dart';
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

  testWidgets('onboarding starts at once with an address from discovery', (
    tester,
  ) async {
    Uri? asked;
    await tester.pumpWidget(
      MaterialApp(
        home: OnboardingScreen(
          client: _NoHub(),
          cameraUri: Uri.parse('http://192.168.1.77:8080'),
          name: 'Garten',
          setupClientFor: (base) {
            asked = base;
            return CameraSetupClient(
              base,
              httpClient: MockClient((_) async => http.Response('', 503)),
            );
          },
        ),
      ),
    );
    await tester.pump();
    expect(asked.toString(), 'http://192.168.1.77:8080');
    expect(find.text('Suche die Kamera …'), findsOneWidget);
    await tester.pumpWidget(
      const SizedBox(),
    ); // dispose: stops the polling timer
  });

  testWidgets('after pairing, a camera without location gets a clear hint', (
    tester,
  ) async {
    final setupJson = {
      'device_id': 'aaaaaaaaaaaaaaaaaaaaaaaaaa',
      'hub_url': 'https://hub.example',
      'paired': true,
      'location_set': false,
    };
    final hub = HubClient(
      baseUrl: 'https://hub.example',
      token: 'tok',
      httpClient: MockClient(
        (_) async => http.Response(jsonEncode(cameraJson), 200),
      ),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => OnboardingScreen(
                  client: hub,
                  cameraUri: Uri.parse('http://192.168.1.77:8080'),
                  setupClientFor: (base) => CameraSetupClient(
                    base,
                    httpClient: MockClient(
                      (_) async => http.Response(jsonEncode(setupJson), 200),
                    ),
                  ),
                ),
              ),
            ),
            child: const Text('start'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('start'));
    // The spinner keeps animating, so pump step by step instead of settling.
    for (var i = 0; i < 10; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
    expect(find.text('Standort fehlt'), findsOneWidget);
    await tester.tap(find.text('Verstanden'));
    for (var i = 0; i < 10; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
    expect(find.text('start'), findsOneWidget); // back on the list
  });
}

class _NoHub extends HubClient {
  _NoHub() : super(baseUrl: 'https://hub.example', token: 'tok');
}
