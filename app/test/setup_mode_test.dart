import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:allskyhub_app/api/camera_setup.dart';
import 'package:allskyhub_app/api/discovery.dart';
import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/api/setup_key.dart';
import 'package:allskyhub_app/api/setup_mode.dart';
import 'package:allskyhub_app/platform/location.dart';
import 'package:allskyhub_app/platform/wifi_binding.dart';
import 'package:allskyhub_app/screens/setup_mode_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:nsd/nsd.dart' as nsd;

const deviceId = 'abcdaaaaaaaaaaaaaaaaaaaaaa';
const hubUrl = 'https://hub.example';

class FakeBinding implements WifiBinding {
  final calls = <String>[];
  final joined = <SetupWifi>[];
  JoinResult joinResult = JoinResult.joined;

  @override
  Future<JoinResult> joinSetup(SetupWifi wifi) async {
    joined.add(wifi);
    calls.add('join');
    return joinResult;
  }

  @override
  Future<bool> bindToWifi() async {
    calls.add('bind');
    return true;
  }

  @override
  Future<void> unbind() async => calls.add('unbind');
}

class FakeLocation implements LocationSource {
  LocationResult result = LocationResult.found(
    CameraLocation(48.3058, 14.2864),
  );

  @override
  Future<LocationResult> current() async => result;

  @override
  Future<String?> timezone() async => 'Europe/Vienna';
}

class FakeDiscovery implements CameraDiscovery {
  Uri? result;
  String? askedFor;

  @override
  Future<Uri?> find(String deviceId, {Duration timeout = Duration.zero}) async {
    askedFor = deviceId;
    return result;
  }
}

class Fixture {
  Map<String, Object?> setup = {
    'device_id': deviceId,
    'hub_url': 'https://allskyhub.org',
    'paired': false,
    'pairing_code': null,
    'expires_in': null,
    'setup_mode': true,
    'last_error': null,
  };
  bool reachable = true;
  int networkStatus = 202;
  Map<String, dynamic>? sent;
  final binding = FakeBinding();
  final discovery = FakeDiscovery();
  final location = FakeLocation();

  late final controller = SetupModeController(
    hub: HubClient(baseUrl: hubUrl, token: 'tok'),
    binding: binding,
    discovery: discovery,
    locationSource: location,
    setup: CameraSetupClient(
      setupModeUri,
      httpClient: MockClient((request) async {
        if (!reachable) throw http.ClientException('no route');
        return switch ((request.method, request.url.path)) {
          ('GET', '/api/setup') => http.Response(jsonEncode(setup), 200),
          ('GET', '/api/wifi/networks') => http.Response(
            jsonEncode([
              {'ssid': 'Home', 'signal': 80, 'secure': true},
              {'ssid': 'Guest', 'signal': 40, 'secure': false},
            ]),
            200,
          ),
          ('POST', '/api/setup/network') => () {
            sent = jsonDecode(request.body) as Map<String, dynamic>;
            return http.Response(
              jsonEncode({'will_join': sent!['ssid']}),
              networkStatus,
            );
          }(),
          _ => http.Response('', 404),
        };
      }),
    ),
  );
}

void main() {
  test('setup network name comes from the device id', () {
    final info = SetupInfo.fromJson({
      'device_id': deviceId,
      'hub_url': hubUrl,
      'paired': false,
    });
    expect(info.setupSsid, 'allskyhub-ABCD');
    expect(setupModeUri.toString(), 'http://10.42.0.1:8080');
  });

  test(
    'connect, send the network with the app hub, find the camera at home',
    () async {
      final f = Fixture()
        ..discovery.result = Uri.parse('http://192.168.1.77:8080');
      await f.controller.connect();
      expect(f.controller.phase, SetupPhase.chooseNetwork);
      expect(f.controller.networks.map((n) => n.ssid), ['Home', 'Guest']);
      expect(f.binding.calls, ['bind']);

      expect(f.controller.timezone, 'Europe/Vienna');
      await f.controller.locate();
      expect(f.controller.location?.latitude, 48.31); // rounded to about 1 km
      await f.controller.send(ssid: 'Home', password: 'secret', country: 'de');
      expect(f.sent, {
        'ssid': 'Home',
        'password': 'secret',
        'country': 'DE',
        'hub_url': hubUrl,
        'latitude': 48.31,
        'longitude': 14.29,
        'timezone': 'Europe/Vienna',
      });
      expect(f.binding.calls, ['bind', 'unbind']);
      expect(f.discovery.askedFor, deviceId);
      expect(f.controller.phase, SetupPhase.found);
      expect(f.controller.cameraUri.toString(), 'http://192.168.1.77:8080');
    },
  );

  test('open networks are sent without password', () async {
    final f = Fixture();
    await f.controller.connect();
    await f.controller.send(ssid: 'Guest', country: 'AT');
    expect(f.sent!.containsKey('password'), isFalse);
    expect(f.controller.phase, SetupPhase.notFound);
  });

  test('shows the last error after a failed attempt', () async {
    final f = Fixture()..setup['last_error'] = 'wifi_auth';
    await f.controller.connect();
    expect(f.controller.phase, SetupPhase.chooseNetwork);
    expect(f.controller.error, contains('WLAN-Passwort'));
  });

  test(
    'unreachable camera or one not in setup mode stays at the first step',
    () async {
      final f = Fixture()..reachable = false;
      await f.controller.connect();
      expect(f.controller.phase, SetupPhase.joinCameraNetwork);
      expect(f.controller.error, contains('nicht erreichbar'));
      expect(f.binding.calls, ['bind', 'unbind']);

      final g = Fixture()..setup['setup_mode'] = false;
      await g.controller.connect();
      expect(g.controller.phase, SetupPhase.joinCameraNetwork);
      expect(g.controller.error, contains('nicht im Einrichtungsmodus'));
    },
  );

  test('a rejected network keeps the choice open', () async {
    final f = Fixture()..networkStatus = 400;
    await f.controller.connect();
    await f.controller.send(ssid: 'Home', password: 'x', country: 'DE');
    expect(f.controller.phase, SetupPhase.chooseNetwork);
    expect(f.controller.error, contains('nicht angenommen'));
    expect(f.binding.calls, ['bind']);
  });

  test('discovery matches the TXT id and prefers the IPv4 address', () {
    final service = nsd.Service(
      name: 'allskyhub-abcd',
      type: serviceType,
      host: 'allskyhub-abcd.local',
      port: 8080,
      txt: {
        'id': Uint8List.fromList(utf8.encode(deviceId)),
        'v': Uint8List.fromList([0x31]),
      },
      addresses: [InternetAddress('192.168.1.77')],
    );
    expect(
      matchService(service, deviceId).toString(),
      'http://192.168.1.77:8080',
    );
    expect(matchService(service, 'b' * 26), isNull);
    const noTxt = nsd.Service(
      name: 'x',
      type: serviceType,
      host: 'x.local',
      port: 8080,
    );
    expect(matchService(noTxt, deviceId), isNull);
  });

  testWidgets('screen: choose the network, password rules, send', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(
      400,
      2400,
    ); // the whole form on one screen
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final f = Fixture();
    await tester.pumpWidget(
      MaterialApp(home: SetupModeScreen(controller: f.controller)),
    );
    await tester.tap(find.byKey(const Key('joined-manually')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Verbunden mit allskyhub-ABCD'), findsOneWidget);

    await tester.tap(find.text('Home'));
    await tester.pump();
    await tester.enterText(find.byKey(const Key('country')), 'de');
    await tester.enterText(find.byKey(const Key('wifi-password')), 'short');
    await tester.pump();
    final send = find.widgetWithText(FilledButton, 'Kamera verbinden');
    expect(tester.widget<FilledButton>(send).onPressed, isNull);

    await tester.enterText(
      find.byKey(const Key('wifi-password')),
      'long enough',
    );
    await tester.pump();
    final sendButton = find.widgetWithText(FilledButton, 'Kamera verbinden');
    expect(
      tester.widget<FilledButton>(sendButton).onPressed,
      isNull,
    ); // no location yet
    f.location.result = const LocationResult.missing(LocationProblem.denied);
    await tester.ensureVisible(find.byKey(const Key('locate')));
    await tester.tap(find.byKey(const Key('locate')));
    await tester.pump();
    expect(
      find.textContaining('Gib die Koordinaten der Kamera ein'),
      findsOneWidget,
    );
    await tester.enterText(find.byKey(const Key('latitude')), '47,8');
    await tester.enterText(find.byKey(const Key('longitude')), '13.05');
    await tester.pump();
    await tester.ensureVisible(send);
    await tester.tap(send);
    await tester.pumpAndSettle();
    expect(f.sent, containsPair('password', 'long enough'));
    expect(f.sent, containsPair('latitude', 47.8));
    expect(f.sent, containsPair('longitude', 13.05));
    expect(f.sent, containsPair('timezone', 'Europe/Vienna'));
    expect(
      find.text('Weiter suchen'),
      findsOneWidget,
    ); // discovery found nothing
  });

  test('manual coordinates are checked and rounded', () {
    expect(CameraLocation.tryParse('47,812', '13.049')?.latitude, 47.81);
    expect(CameraLocation.tryParse('91', '13'), isNull);
    expect(CameraLocation.tryParse('47', 'x'), isNull);
    expect(CameraLocation.tryParse('-33.9', '151.2')?.longitude, 151.2);
  });

  test('setup info reports a missing location', () {
    final info = SetupInfo.fromJson({
      'device_id': deviceId,
      'hub_url': hubUrl,
      'paired': true,
      'location_set': false,
      'timezone': null,
      'camera': 'auto',
    });
    expect(info.locationSet, isFalse);
    expect(info.camera, 'auto');
    final old = SetupInfo.fromJson({
      'device_id': deviceId,
      'hub_url': hubUrl,
      'paired': true,
    });
    expect(old.locationSet, isTrue); // older agents: no false alarm
  });

  group('setup key (SPEC §7.1)', () {
    test('the Wi-Fi QR code on the label', () {
      final wifi = parseWifiQr('WIFI:T:WPA;S:allskyhub-ABCD;P:ABCDEFGHJKMN;;');
      expect(wifi?.ssid, 'allskyhub-ABCD');
      expect(wifi?.key, 'ABCDEFGHJKMN');
      // Any order, H: optional, escapes.
      final other = parseWifiQr(
        r'WIFI:P:AB\;CD\\EFGHJ;H:false;S:allskyhub-WXYZ;T:WPA;;',
      );
      expect(other?.ssid, 'allskyhub-WXYZ');
      expect(other?.key, r'AB;CD\EFGHJ');
      expect(parseWifiQr('WIFI:T:WPA;S:HomeNet;P:secret123;;'), isNull);
      expect(parseWifiQr('WIFI:T:nopass;S:allskyhub-ABCD;;'), isNull);
      expect(
        parseWifiQr('WIFI:T:WEP;S:allskyhub-ABCD;P:ABCDEFGHJKMN;;'),
        isNull,
      );
      expect(parseWifiQr('https://allskyhub.org'), isNull);
    });

    test('a typed key', () {
      expect(normalizeSetupKey('abcd-efgh-jkmn'), 'ABCDEFGHJKMN');
      expect(normalizeSetupKey(' ABCD EFGH JKMN '), 'ABCDEFGHJKMN');
      expect(normalizeSetupKey('ABCD-EFGH-JKM'), isNull);
      expect(normalizeSetupKey('ABCD-EFGH-JKM0'), isNull); // no 0, 1, I, L, O
    });

    test('joined from the app: no second bind, then the networks', () async {
      final f = Fixture();
      await f.controller.join(const SetupWifi(key: 'ABCDEFGHJKMN'));
      expect(f.controller.phase, SetupPhase.chooseNetwork);
      expect(f.binding.calls, ['join']);
      expect(f.binding.joined.single.ssid, isNull); // any allskyhub-… network
      expect(f.controller.outdatedCamera, isFalse);
    });

    test('an older camera with an open network is flagged', () async {
      final f = Fixture();
      await f.controller.join(const SetupWifi());
      expect(f.controller.phase, SetupPhase.chooseNetwork);
      expect(f.controller.outdatedCamera, isTrue);
    });

    test('failed or impossible joins explain what to do', () async {
      final f = Fixture()..binding.joinResult = JoinResult.failed;
      await f.controller.join(const SetupWifi(key: 'ABCDEFGHJKMN'));
      expect(f.controller.phase, SetupPhase.joinCameraNetwork);
      expect(f.controller.error, contains('Stimmt der Schlüssel'));
      f.binding.joinResult = JoinResult.unsupported;
      await f.controller.join(
        const SetupWifi(ssid: 'allskyhub-ABCD', key: 'ABCDEFGHJKMN'),
      );
      expect(
        f.controller.error,
        contains('„allskyhub-ABCD“ (Schlüssel ABCD-EFGH-JKMN)'),
      );
    });

    test('the hub must be https', () async {
      final f = Fixture();
      final insecure = SetupModeController(
        hub: HubClient(baseUrl: 'http://hub.example', token: 'tok'),
        binding: f.binding,
        discovery: f.discovery,
        locationSource: f.location,
        setup: f.controller.setup,
      );
      await insecure.connect();
      await insecure.send(ssid: 'Home', password: 'secret123', country: 'AT');
      expect(insecure.phase, SetupPhase.chooseNetwork);
      expect(insecure.error, contains('https://'));
      expect(f.sent, isNull);
    });

    testWidgets('scan the label, or type the key', (tester) async {
      final f = Fixture();
      await tester.pumpWidget(
        MaterialApp(
          home: SetupModeScreen(
            controller: f.controller,
            scanner: () => Builder(
              builder: (context) => TextButton(
                key: const Key('fake-scan'),
                onPressed: () => Navigator.of(
                  context,
                ).pop('WIFI:T:WPA;S:allskyhub-ABCD;P:ABCDEFGHJKMN;;'),
                child: const Text('scan'),
              ),
            ),
          ),
        ),
      );
      await tester.enterText(
        find.byKey(const Key('setup-key')),
        'abcd-efgh-jkm',
      );
      await tester.tap(find.byKey(const Key('join-typed')));
      await tester.pumpAndSettle();
      expect(find.textContaining('12 Zeichen'), findsOneWidget);
      expect(f.binding.joined, isEmpty);

      await tester.tap(find.byKey(const Key('scan-qr')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('fake-scan')));
      await tester.pumpAndSettle();
      expect(f.binding.joined.single.ssid, 'allskyhub-ABCD');
      expect(f.controller.phase, SetupPhase.chooseNetwork);
    });
  });
}
