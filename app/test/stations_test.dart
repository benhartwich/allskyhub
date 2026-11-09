// SharedSky in the app (station_screens.dart): the way in, "Kamera verbinden" for the three
// kinds of software, and stations on "Meine Kameras".

import 'dart:convert';
import 'dart:typed_data';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/platform/location.dart';
import 'package:allskyhub_app/screens/cameras_screen.dart';
import 'package:allskyhub_app/screens/join_screen.dart';
import 'package:allskyhub_app/screens/station_screens.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _Here implements LocationSource {
  @override
  Future<LocationResult> current() async =>
      LocationResult.found(CameraLocation(47.80, 3.11));

  @override
  Future<String?> timezone() async => 'Europe/Vienna';
}

http.Response _json(Object body, [int status = 200]) =>
    http.Response.bytes(utf8.encode(jsonEncode(body)), status);

Map<String, Object?> _station(
  String software, {
  String state = 'waiting',
  String id = '01STATION0000000000000000',
}) => {
  'id': id,
  'name': 'Gartensternwarte',
  'software': software,
  'state': state,
  'url': software == 'image-url' ? 'https://cam.example.org/image.jpg' : null,
  'frames_today': 12,
  'meteors_today': 1,
};

/// A hub that records what the app sent.
class _Hub {
  _Hub({this.open = true, this.reject});

  final bool open;

  /// A sentence the hub answers "Kamera verbinden" with (400).
  final String? reject;
  final sent = <http.Request>[];
  final stations = <Map<String, Object?>>[];

  HubClient client({String? token = 't'}) => HubClient(
    baseUrl: 'https://hub.example',
    token: token,
    httpClient: MockClient((request) async {
      sent.add(request);
      final path = request.url.path;
      if (path == '/api/v1/public/hub') {
        return _json({
          'open_signup': open,
          'signup_url': open
              ? 'https://hub.example/konto/anlegen?weiter=sharedsky'
              : 'https://hub.example/zugang',
        });
      }
      if (path == '/api/v1/stations' && request.method == 'POST') {
        if (reject != null) return _json({'detail': reject}, 400);
        final software = RegExp(r'name="software"\r\n\r\n([a-z-]+)').firstMatch(
          latin1.decode(request.bodyBytes),
        )![1]!; // a photo is binary
        final station = _station(
          software,
          state: software == 'image-url' ? 'pending' : 'waiting',
        );
        stations.add(station);
        return _json({
          'station': station,
          'secret': software == 'image-url' ? null : 'f' * 64,
          'base_url': software == 'indi-allsky'
              ? 'https://hub.example/indi-allsky'
              : null,
          'docs_url': 'https://hub.example/entwickler/connect',
        });
      }
      if (path == '/api/v1/stations') return _json(stations);
      if (path == '/api/v1/cameras') return _json(<Object>[]);
      if (path.endsWith('/delete')) {
        final password = (jsonDecode(request.body) as Map)['password'];
        if (password != 'richtig-richtig') {
          return _json({'detail': 'Das Passwort stimmt nicht.'}, 400);
        }
        stations.clear();
        return http.Response('', 204);
      }
      return http.Response('', 404);
    }),
  );
}

Widget _app(Widget home, {VoidCallback? signIn}) => MaterialApp(
  builder: (context, child) =>
      signIn == null ? child! : SignInScope(onSignIn: signIn, child: child!),
  home: home,
);

void _tall(WidgetTester tester) {
  tester.view.physicalSize = const Size(800, 2400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

void main() {
  testWidgets('guests go through the way in to an account first', (
    tester,
  ) async {
    _tall(tester);
    var asked = false;
    final hub = _Hub();
    await tester.pumpWidget(
      _app(
        JoinScreen(client: hub.client(token: null), location: _Here()),
        signIn: () => asked = true,
      ),
    );
    await tester.tap(find.byKey(const Key('join-own-camera')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('wizard-camera-yes')));
    await tester.pumpAndSettle();
    expect(find.text('Welche Software läuft darauf?'), findsOneWidget);
    await tester.tap(find.byKey(const Key('wizard-software-indi-allsky')));
    await tester.pumpAndSettle();
    expect(find.text('Mitmachen (SharedSky)'), findsOneWidget);
    expect(find.textContaining('neben indi-allsky'), findsOneWidget);
    await tester.tap(find.byKey(const Key('wizard-join')));
    await tester.pumpAndSettle();
    expect(find.text('Kostenloses Konto anlegen'), findsOneWidget);
    await tester.tap(find.byKey(const Key('gate-sign-in')));
    expect(asked, isTrue);
  });

  testWidgets('with sign-up closed the gate asks for access', (tester) async {
    await tester.pumpWidget(
      _app(AccountGateScreen(client: _Hub(open: false).client(token: null))),
    );
    await tester.pumpAndSettle();
    expect(find.text('Zugang anfragen'), findsOneWidget);
  });

  testWidgets('indi-allsky: the three values, shown once', (tester) async {
    _tall(tester);
    final hub = _Hub();
    await tester.pumpWidget(
      _app(StationConnectScreen(client: hub.client(), software: 'indi-allsky')),
    );
    await tester.enterText(
      find.byKey(const Key('station-name')),
      'Gartensternwarte',
    );
    await tester.tap(find.byKey(const Key('station-terms')));
    await tester.tap(find.byKey(const Key('station-create')));
    await tester.pumpAndSettle();
    expect(find.text('In indi-allsky eintragen'), findsOneWidget);
    expect(find.text('https://hub.example/indi-allsky'), findsOneWidget);
    expect(find.text('01STATION0000000000000000'), findsOneWidget);
    expect(find.text('f' * 64), findsOneWidget);
    expect(find.textContaining('nur jetzt sichtbar'), findsOneWidget);
    final body = hub.sent.last.body;
    expect(body, contains('name="terms"\r\n\r\ntrue'));
    expect(body, isNot(contains('name="url"'))); // only for an image address
  });

  testWidgets('other software: ID, secret and the developer docs', (
    tester,
  ) async {
    _tall(tester);
    await tester.pumpWidget(
      _app(StationConnectScreen(client: _Hub().client(), software: 'other')),
    );
    await tester.enterText(find.byKey(const Key('station-name')), 'Eigenbau');
    await tester.tap(find.byKey(const Key('station-terms')));
    await tester.tap(find.byKey(const Key('station-create')));
    await tester.pumpAndSettle();
    expect(find.text('Station-ID'), findsOneWidget);
    expect(find.text('Entwickler-Doku öffnen'), findsOneWidget);
  });

  testWidgets('by the image address: place from the phone, then the review', (
    tester,
  ) async {
    _tall(tester);
    final hub = _Hub();
    await tester.pumpWidget(
      _app(
        StationConnectScreen(
          client: hub.client(),
          software: 'image-url',
          location: _Here(),
          precise: () async => (lat: 47.80321, lon: 3.10987, elevation: 1287.4),
          pickPhoto: () async =>
              (Uint8List.fromList([0xff, 0xd8, 0xff, 0xd9]), 'beweis.jpg'),
        ),
      ),
    );
    await tester.pumpAndSettle();
    final timezone = tester.widget<TextField>(
      find.byKey(const Key('station-timezone')),
    );
    expect(timezone.controller!.text, 'Europe/Vienna'); // from the phone
    await tester.enterText(find.byKey(const Key('station-name')), 'Gaisberg');
    await tester.enterText(
      find.byKey(const Key('station-url')),
      'https://cam.example.org/image.jpg',
    );
    await tester.tap(find.byKey(const Key('station-here')));
    await tester.pumpAndSettle();
    expect(find.text('47.80321'), findsOneWidget); // exact, not rounded
    expect(find.text('1287'), findsOneWidget);
    await tester.enterText(
      find.byKey(const Key('station-proof')),
      'Meine Allsky-Seite: https://cam.example.org',
    );
    await tester.ensureVisible(find.text('Bild wählen'));
    await tester.tap(find.text('Bild wählen'));
    await tester.pumpAndSettle();
    expect(find.text('beweis.jpg'), findsOneWidget);
    await tester.ensureVisible(find.byKey(const Key('station-terms')));
    await tester.tap(find.byKey(const Key('station-terms')));
    await tester.ensureVisible(find.byKey(const Key('station-create')));
    await tester.tap(find.byKey(const Key('station-create')));
    await tester.pumpAndSettle();
    expect(find.text('wartet auf Prüfung'), findsOneWidget);
    // The station's page then asks for its part in the network: the POST before it.
    final post = hub.sent.lastWhere((r) => r.method == 'POST');
    final body = latin1.decode(post.bodyBytes);
    expect(body, contains('name="latitude"\r\n\r\n47.80321'));
    expect(body, contains('name="timezone"\r\n\r\nEurope/Vienna'));
    expect(body, contains('filename="beweis.jpg"'));
    // The defaults of what the station shows (SPEC §10.6).
    expect(body, contains('name="page"\r\n\r\ntrue'));
    expect(body, contains('name="images"\r\n\r\nfalse'));
  });

  testWidgets("the hub's sentence when something is missing", (tester) async {
    _tall(tester);
    await tester.pumpWidget(
      _app(
        StationConnectScreen(
          client: _Hub(
            reject: 'Bitte den Teilnahmebedingungen zustimmen.',
          ).client(),
          software: 'other',
        ),
      ),
    );
    await tester.tap(find.byKey(const Key('station-create')));
    await tester.pumpAndSettle();
    expect(
      find.text('Bitte den Teilnahmebedingungen zustimmen.'),
      findsOneWidget,
    );
  });

  testWidgets('stations on "Meine Kameras", and removing one', (tester) async {
    _tall(tester);
    final hub = _Hub()..stations.add(_station('image-url', state: 'pending'));
    await tester.pumpWidget(
      _app(CamerasScreen(client: hub.client(), onSignOut: () async {})),
    );
    await tester.pumpAndSettle();
    expect(find.text('SharedSky-Stationen'), findsOneWidget);
    expect(find.text('Gartensternwarte'), findsOneWidget);
    expect(find.text('wartet auf Prüfung'), findsOneWidget);

    await tester.tap(find.text('Gartensternwarte'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('station-renew')), findsNothing); // no secret
    await tester.tap(find.byKey(const Key('station-remove')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const Key('station-password')),
      'richtig-richtig',
    );
    await tester.tap(find.byKey(const Key('station-password-ok')));
    await tester.pumpAndSettle();
    expect(find.text('SharedSky-Stationen'), findsNothing);
    expect(find.textContaining('Noch keine Kamera'), findsOneWidget);
  });

  testWidgets('"Kamera hinzufügen" also connects a station', (tester) async {
    _tall(tester);
    await tester.pumpWidget(
      _app(CamerasScreen(client: _Hub().client(), onSignOut: () async {})),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('add-camera')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('add-station')));
    await tester.pumpAndSettle();
    expect(find.text('Welche Software läuft darauf?'), findsOneWidget);
  });
}
