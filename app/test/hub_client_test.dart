import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const cameraJson = {
  'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaa',
  'name': 'Garten',
  'profile': 'zwo-asi678mc',
  'agent_version': '0.1.0',
  'online': true,
  'paired_at': '2026-10-06T20:00:00Z',
  'last_seen_at': '2026-10-06T21:30:00Z',
  'latest_image_at': '2026-10-06T21:29:00Z',
  'status': {
    'mode': 'night',
    'exposure_us': 30000000,
    'gain': 120,
    'time_trusted': true,
  },
  'frame': null,
};

void main() {
  test('login stores the token and sends it as bearer', () async {
    final requests = <http.Request>[];
    final client = HubClient(
      baseUrl: 'https://hub.example',
      httpClient: MockClient((request) async {
        requests.add(request);
        if (request.url.path == '/api/v1/auth/login') {
          return http.Response(
            jsonEncode({'access_token': 'tok', 'token_type': 'bearer'}),
            200,
          );
        }
        return http.Response(jsonEncode([cameraJson]), 200);
      }),
    );
    expect(await client.login('a@example.org', 'secret'), 'tok');
    final cameras = await client.cameras();

    expect(
      jsonDecode(requests.first.body),
      containsPair('email', 'a@example.org'),
    );
    expect(requests.last.headers['Authorization'], 'Bearer tok');
    expect(cameras.single.name, 'Garten');
    expect(cameras.single.online, isTrue);
    expect(cameras.single.status['mode'], 'night');
    expect(cameras.single.latestImageAt, DateTime.utc(2026, 10, 6, 21, 29));
  });

  test('errors carry status and detail', () async {
    final client = HubClient(
      baseUrl: 'https://hub.example',
      token: 'tok',
      httpClient: MockClient(
        (_) async => http.Response(
          jsonEncode({'detail': 'Der Code ist ungültig oder abgelaufen.'}),
          400,
        ),
      ),
    );
    expect(
      () => client.claim('ZZZZZZ'),
      throwsA(
        isA<HubException>()
            .having((e) => e.statusCode, 'status', 400)
            .having((e) => e.message, 'message', contains('ungültig')),
      ),
    );
  });

  test('image url asks for live frames', () {
    final client = HubClient(baseUrl: 'https://hub.example');
    expect(
      client.imageUrl('abc', live: true, bust: 3).toString(),
      'https://hub.example/api/v1/cameras/abc/image/full.jpg?live=true&t=3',
    );
    expect(
      client.imageUrl('abc', thumb: true).path,
      '/api/v1/cameras/abc/image/thumb.jpg',
    );
  });
}
