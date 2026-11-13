import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/api/place_store.dart';
import 'package:allskyhub_app/api/session_store.dart';
import 'package:allskyhub_app/main.dart';
import 'package:allskyhub_app/night_mode.dart';
import 'package:allskyhub_app/platform/location.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'app_test.dart' show fakeHub;

// The group around 50.04 N / 10.39 E (made-up test places, never near a real camera).
const lat = 50.04;
const lon = 10.39;

class _NoLocation implements LocationSource {
  @override
  Future<LocationResult> current() async =>
      const LocationResult.missing(LocationProblem.denied);

  @override
  Future<String?> timezone() async => null;
}

Widget _app(MemoryNightStore night, {NightPlace? place}) => AllskyApp(
  store: MemorySessionStore(),
  places: MemoryPlaceStore(),
  location: _NoLocation(),
  nightStore: night,
  nightPlace: place ?? () async => null,
  clientFor: (url, token) =>
      HubClient(baseUrl: url, token: token, httpClient: fakeHub()),
);

bool _red(WidgetTester tester) =>
    find.byWidgetPredicate((w) => w is ColorFiltered).evaluate().isNotEmpty;

void main() {
  test('the sun: high at summer noon, low at midnight, near 0 at sunset', () {
    // Local noon at 10.39° E is about 11:18 UTC.
    expect(
      sunAltitude(DateTime.utc(2026, 6, 21, 11, 18), lat, lon),
      closeTo(63.4, 0.5),
    );
    expect(
      sunAltitude(DateTime.utc(2026, 12, 21, 23, 18), lat, lon),
      closeTo(-63.4, 0.5),
    );
    // The hub's allskyhub_sky.astro.sun_altitude gives -2.28° and -13.34°.
    expect(
      sunAltitude(DateTime.utc(2026, 11, 13, 15, 47), lat, lon),
      closeTo(-2.28, 0.1),
    );
    expect(
      sunAltitude(DateTime.utc(2026, 11, 13, 17), lat, lon),
      closeTo(-13.34, 0.1),
    );
  });

  test(
    'automatisch follows the sun at the place, and stays off without one',
    () async {
      var now = DateTime.utc(2026, 11, 13, 12);
      (double, double)? place = (lat, lon);
      final mode = NightMode(
        store: MemoryNightStore(NightSetting.auto),
        place: () async => place,
        clock: () => now,
      );
      await mode.load();
      expect(mode.active, isFalse);
      now = DateTime.utc(2026, 11, 13, 17); // sun about -13°
      await mode.refresh();
      expect(mode.active, isTrue);
      expect(mode.placeKnown, isTrue);
      place = null;
      await mode.refresh();
      expect(mode.active, isFalse);
      expect(mode.placeKnown, isFalse);
      mode.dispose();
    },
  );

  testWidgets('the whole app turns red, and the moon switches it', (
    tester,
  ) async {
    final night = MemoryNightStore(NightSetting.on);
    await tester.pumpWidget(_app(night));
    await tester.pumpAndSettle();
    expect(_red(tester), isTrue);

    await tester.tap(find.byType(NightButton).first);
    await tester.pumpAndSettle();
    expect(_red(tester), isFalse);
    expect(night.setting, NightSetting.off);

    await tester.tap(find.byType(NightButton).first);
    await tester.pumpAndSettle();
    expect(_red(tester), isTrue);
    expect(night.setting, NightSetting.on);
  });

  testWidgets('long press offers automatisch', (tester) async {
    final night = MemoryNightStore();
    await tester.pumpWidget(_app(night));
    await tester.pumpAndSettle();
    expect(_red(tester), isFalse);

    await tester.longPress(find.byType(NightButton).first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Automatisch nachts'));
    await tester.pumpAndSettle();
    expect(night.setting, NightSetting.auto);
    // No place known: it says what to do.
    expect(
      find.textContaining('Wähle unter „Himmel“ einen Ort'),
      findsOneWidget,
    );
  });
}
