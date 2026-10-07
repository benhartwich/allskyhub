import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/screens/gallery_screens.dart';
import 'package:allskyhub_app/screens/product_screens.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'hub_client_test.dart' show cameraJson;

const productsJson = [
  {
    'kind': 'keogram',
    'name': 'keogram.jpg',
    'content_type': 'image/jpeg',
    'size': 300000,
    'duration_s': null,
    'has_full': true,
    'has_thumb': true,
    'pending': false,
  },
  {
    'kind': 'timelapse',
    'name': 'timelapse.mp4',
    'content_type': 'video/mp4',
    'size': 250 * 1024 * 1024,
    'duration_s': 95.4,
    'has_full': false,
    'has_thumb': true,
    'pending': false,
  },
];

/// A hub whose timelapse arrives after `readyAfter` requests.
class FakeHub {
  FakeHub({this.readyAfter = 2});

  final int readyAfter;
  int videoRequests = 0;

  HubClient get client => HubClient(
    baseUrl: 'https://hub.example',
    token: 'tok',
    httpClient: MockClient((request) async {
      final path = request.url.path;
      if (path.endsWith('/nights')) {
        return http.Response(
          jsonEncode([
            {
              'night_id': '20261006',
              'frames': 0,
              'first': null,
              'last': null,
              'products': 2,
            },
          ]),
          200,
        );
      }
      if (path.endsWith('/frames')) return http.Response('[]', 200);
      if (path.endsWith('/products')) {
        return http.Response(jsonEncode(productsJson), 200);
      }
      if (path.endsWith('/timelapse.mp4') &&
          request.url.queryParameters.isEmpty) {
        videoRequests++;
        return videoRequests > readyAfter
            ? http.Response(
                'video',
                200,
                headers: {'content-type': 'video/mp4'},
              )
            : http.Response('{"status": "requested"}', 202);
      }
      return http.Response('', 404);
    }),
  );
}

void main() {
  test(
    'client parses products and asks for missing ones without downloading',
    () async {
      final hub = FakeHub(readyAfter: 1);
      final client = hub.client;
      final products = await client.products('cam', '20261006');
      expect(products.map((p) => p.kind), ['keogram', 'timelapse']);
      expect(products.last.isVideo, isTrue);
      expect(products.last.durationS, 95.4);
      expect(
        await client.ensureProduct('cam', '20261006', 'timelapse.mp4'),
        isFalse,
      );
      expect(
        await client.ensureProduct('cam', '20261006', 'timelapse.mp4'),
        isTrue,
      );
      expect(
        client
            .productUrl('cam', '20261006', 'keogram.jpg', thumb: true)
            .toString(),
        'https://hub.example/api/v1/cameras/cam/products/20261006/keogram.jpg?variant=thumb',
      );
    },
  );

  test('fetcher polls until the hub has the product', () async {
    final hub = FakeHub(readyAfter: 2);
    final fetcher = ProductFetcher(
      hub.client,
      'cam',
      '20261006',
      'timelapse.mp4',
      interval: Duration.zero,
    );
    expect(await fetcher.run(), isTrue);
    expect(hub.videoRequests, 3);
  });

  test('formatting', () {
    expect(formatSize(250 * 1024 * 1024), '250.0 MB');
    expect(formatSize(300000), '293 KB');
    expect(formatDuration(95.4), '1:35');
    final productsOnly = Night.fromJson({
      'night_id': '20261006',
      'frames': 0,
      'first': null,
      'last': null,
      'products': 3,
    });
    expect(nightSubtitle(productsOnly), '3 Produkte');
  });

  testWidgets(
    'night with only products shows the strip; video waits for the camera',
    (tester) async {
      final hub = FakeHub(readyAfter: 1000);
      await tester.pumpWidget(
        MaterialApp(
          home: NightsScreen(
            client: hub.client,
            camera: Camera.fromJson(cameraJson),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('2 Produkte'), findsOneWidget);

      await tester.tap(find.text('2 Produkte'));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('keogram.jpg')), findsOneWidget);
      expect(
        find.textContaining('Zeitraffer · 1:35 · 250.0 MB'),
        findsOneWidget,
      );

      await tester.tap(find.byKey(const ValueKey('timelapse.mp4')));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(find.textContaining('Wird von der Kamera geholt'), findsOneWidget);
      expect(hub.videoRequests, greaterThan(0));
      await tester.pumpWidget(const SizedBox()); // cancels the polling
      await tester.pump(const Duration(seconds: 4));
    },
  );
}
