import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  test('the hub names its newest test build', () async {
    final client = HubClient(
      baseUrl: 'https://hub.example',
      httpClient: MockClient((request) async {
        expect(
          request.url.toString(),
          'https://hub.example/app/android/test.json',
        );
        return http.Response(
          jsonEncode({
            'build': 412,
            'version': '1.0.0',
            'url': '/app/android/allskyhub-test-412.apk',
          }),
          200,
        );
      }),
    );
    final build = await client.testBuild();
    expect(build!.build, 412);
    expect(
      build.apk.toString(),
      'https://hub.example/app/android/allskyhub-test-412.apk',
    );
    expect(build.newerThan(411), isTrue);
    expect(build.newerThan(412), isFalse);
  });

  test('no test build: null', () async {
    final client = HubClient(
      baseUrl: 'https://hub.example',
      httpClient: MockClient((_) async => http.Response('', 404)),
    );
    expect(await client.testBuild(), isNull);
  });
}
