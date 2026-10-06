import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:allskyhub_app/api/camera_setup.dart';
import 'package:allskyhub_app/api/discovery.dart';
import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/api/setup_mode.dart';
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

  @override
  Future<bool> bindToWifi() async {
    calls.add('bind');
    return true;
  }

  @override
  Future<void> unbind() async => calls.add('unbind');
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

  late final controller = SetupModeController(
    hub: HubClient(baseUrl: hubUrl, token: 'tok'),
    binding: binding,
    discovery: discovery,
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

      await f.controller.send(ssid: 'Home', password: 'secret', country: 'de');
      expect(f.sent, {
        'ssid': 'Home',
        'password': 'secret',
        'country': 'DE',
        'hub_url': hubUrl,
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
    final f = Fixture();
    await tester.pumpWidget(
      MaterialApp(home: SetupModeScreen(controller: f.controller)),
    );
    await tester.tap(find.text('Weiter'));
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
    await tester.ensureVisible(send);
    await tester.tap(send);
    await tester.pumpAndSettle();
    expect(f.sent, containsPair('password', 'long enough'));
    expect(
      find.text('Weiter suchen'),
      findsOneWidget,
    ); // discovery found nothing
  });
}
