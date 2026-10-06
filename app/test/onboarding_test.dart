import 'dart:convert';

import 'package:allskyhub_app/api/camera_setup.dart';
import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/api/onboarding.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'hub_client_test.dart' show cameraJson;

const hubUrl = 'https://hub.example';
const deviceId = 'aaaaaaaaaaaaaaaaaaaaaaaaaa';

/// A camera whose setup API answers from mutable state, and a hub that records claims.
class Fixture {
  Map<String, Object?> setup = {
    'device_id': deviceId,
    'hub_url': hubUrl,
    'paired': false,
    'pairing_code': null,
    'expires_in': null,
    'profile': 'zwo-asi678mc',
    'agent_version': '0.1.0',
  };
  bool cameraReachable = true;
  bool isCamera = true;
  int claimStatus = 200;
  final claims = <Map<String, dynamic>>[];

  late final controller = OnboardingController(
    setup: CameraSetupClient(
      Uri.parse('http://192.168.1.50:8080'),
      httpClient: MockClient((request) async {
        expect(request.url.toString(), 'http://192.168.1.50:8080/api/setup');
        if (!cameraReachable) throw http.ClientException('no route to host');
        if (!isCamera) return http.Response('not found', 404);
        return http.Response(jsonEncode(setup), 200);
      }),
    ),
    hub: HubClient(
      baseUrl: hubUrl,
      token: 'tok',
      httpClient: MockClient((request) async {
        if (request.url.path == '/api/v1/cameras/claim') {
          claims.add(jsonDecode(request.body) as Map<String, dynamic>);
          return claimStatus == 200
              ? http.Response(jsonEncode(cameraJson), 200)
              : http.Response(
                  jsonEncode({
                    'detail': 'Der Code ist ungültig oder abgelaufen.',
                  }),
                  claimStatus,
                );
        }
        if (request.url.path == '/api/v1/cameras/$deviceId') {
          return http.Response(jsonEncode(cameraJson), 200);
        }
        return http.Response(jsonEncode({'detail': 'Not found'}), 404);
      }),
    ),
    name: 'Garten',
    pairedTimeout: const Duration(milliseconds: 50),
  );
}

void main() {
  test('camera address input becomes the setup base URI', () {
    expect(
      cameraBaseUri('192.168.1.50').toString(),
      'http://192.168.1.50:8080',
    );
    expect(
      cameraBaseUri(' kamera.local:9000 ').toString(),
      'http://kamera.local:9000',
    );
    expect(
      cameraBaseUri('http://10.42.0.1').toString(),
      'http://10.42.0.1:8080',
    );
    expect(sameHub('https://allskyhub.org/', 'https://allskyhub.org'), isTrue);
    expect(sameHub('https://allskyhub.org', 'https://hub.example'), isFalse);
  });

  test(
    'reads the code, claims it once and finishes when the camera is paired',
    () async {
      final f = Fixture();
      f.cameraReachable = false;
      await f.controller.tick();
      expect(f.controller.phase, OnboardingPhase.connecting);

      f.cameraReachable = true;
      await f.controller.tick();
      expect(f.controller.phase, OnboardingPhase.waitingForCode);

      f.setup['pairing_code'] = 'ABCDEF';
      await f.controller.tick();
      expect(f.controller.phase, OnboardingPhase.waitingForCamera);
      expect(f.claims, [
        {'code': 'ABCDEF', 'name': 'Garten'},
      ]);
      await f.controller.tick();
      expect(f.claims, hasLength(1));

      f.setup
        ..['paired'] = true
        ..['pairing_code'] = null;
      await f.controller.tick();
      expect(f.controller.phase, OnboardingPhase.done);
      expect(f.controller.camera?.name, 'Garten');
    },
  );

  test('finishes after the timeout when the camera never confirms', () async {
    final f = Fixture()..setup['pairing_code'] = 'ABCDEF';
    await f.controller.tick();
    expect(f.controller.phase, OnboardingPhase.waitingForCamera);
    await Future<void>.delayed(const Duration(milliseconds: 60));
    await f.controller.tick();
    expect(f.controller.phase, OnboardingPhase.done);
  });

  test('an expired code waits for the next one', () async {
    final f = Fixture()
      ..setup['pairing_code'] = 'ABCDEF'
      ..claimStatus = 400;
    await f.controller.tick();
    expect(f.controller.phase, OnboardingPhase.waitingForCode);
    f
      ..claimStatus = 200
      ..setup['pairing_code'] = 'GHJKMN';
    await f.controller.tick();
    expect(f.controller.phase, OnboardingPhase.waitingForCamera);
    expect(f.claims.map((c) => c['code']), ['ABCDEF', 'GHJKMN']);
  });

  test('refuses a camera that talks to another hub', () async {
    final f = Fixture()
      ..setup['hub_url'] = 'https://other.example'
      ..setup['pairing_code'] = 'ABCDEF';
    await f.controller.tick();
    expect(f.controller.phase, OnboardingPhase.failed);
    expect(f.controller.error, contains('anderen Hub'));
    expect(f.claims, isEmpty);
  });

  test('a camera paired to another account fails', () async {
    final f = Fixture()..setup['paired'] = true;
    f.setup['device_id'] = 'bbbbbbbbbbbbbbbbbbbbbbbbbb';
    await f.controller.tick();
    expect(f.controller.phase, OnboardingPhase.failed);
    expect(f.controller.error, contains('anderen Konto'));
  });

  test('404 on the setup API means no set-up camera at that address', () async {
    final f = Fixture()..isCamera = false;
    await f.controller.tick();
    expect(f.controller.phase, OnboardingPhase.failed);
    expect(
      f.controller.error,
      contains('keine eingerichtete allskyhub-Kamera'),
    );
  });
}
