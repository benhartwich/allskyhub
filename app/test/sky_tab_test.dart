import 'dart:convert';

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/api/place_store.dart';
import 'package:allskyhub_app/platform/location.dart';
import 'package:allskyhub_app/platform/reminders.dart';
import 'package:allskyhub_app/screens/sky_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _Here implements LocationSource {
  final LocationResult result = const LocationResult.missing(
    LocationProblem.denied,
  );
  int asked = 0;

  @override
  Future<LocationResult> current() async {
    asked++;
    return result;
  }

  @override
  Future<String?> timezone() async => 'Europe/Vienna';
}

class _Reminders implements Reminders {
  final ids = <int>{};
  final bodies = <int, String?>{};
  bool allowed = true;

  @override
  Future<Set<int>> pending() async => {...ids};

  @override
  Future<bool> remind(CalendarItem item, {String? forecast}) async {
    if (!allowed || reminderTime(item) == null) return false;
    ids.add(reminderId(item));
    bodies[reminderId(item)] = forecast;
    return true;
  }

  @override
  Future<void> forget(CalendarItem item) async => ids.remove(reminderId(item));

  @override
  Future<void> refresh(
    List<CalendarItem> items,
    Map<DateTime, String> forecasts,
  ) async {
    for (final i in items) {
      if (ids.contains(reminderId(i))) {
        bodies[reminderId(i)] = forecasts[nightOf(i)];
      }
    }
  }
}

Map<String, Object?> _hour(String at, int clouds, {bool good = false}) => {
  'at': at,
  'clouds': clouds,
  'low': 0,
  'mid': 10,
  'high': clouds,
  'rain': 0,
  'wind_kmh': 25.0,
  'moon_up': false,
  'good': good,
};

Map<String, Object?> _night(String evening, String verdict, int good) => {
  'evening': evening,
  'verdict': verdict,
  'summary': 'Klar und dunkel von 01:00 bis 05:00 Uhr.',
  'sunset': '2026-10-28T15:50:00Z',
  'dusk': '2026-10-28T17:30:00Z',
  'dawn': '2026-10-29T04:40:00Z',
  'sunrise': '2026-10-29T06:20:00Z',
  'moonrise': null,
  'moonset': '2026-10-28T23:40:00Z',
  'moon_illumination': 0.34,
  'moon_phase': 'zunehmender Mond',
  'best_start': null,
  'best_end': null,
  'good_hours': good,
  'hours': [
    _hour('2026-10-28T18:00:00Z', 90),
    _hour('2026-10-29T01:00:00Z', 5, good: true),
  ],
};

HubClient _hub(List<Uri> seen) => HubClient(
  baseUrl: 'https://hub.example',
  httpClient: MockClient((request) async {
    seen.add(request.url);
    return switch (request.url.path) {
      '/api/v1/public/sky' => http.Response.bytes(
        utf8.encode(
          jsonEncode({
            'lat': 48.1,
            'lon': 14.4,
            'tz': 'Europe/Vienna',
            'nights': [
              _night('2026-10-28', 'good', 4),
              _night('2026-10-29', 'fair', 2),
              _night('2026-10-30', 'cloudy', 0),
            ],
            'aurora': {
              'now': 2.3,
              'days': [
                {'day': '2026-10-28', 'kp': 3.0, 'predicted': false},
              ],
              'needed': 7.7,
            },
            'passes': <Object>[],
            'nearby': {
              'slug': 'abc',
              'title': 'Lanzenberg',
              'distance_km': 12,
              'at': '2026-10-28T08:00:00Z',
              'cloud_cover': 0.2,
              'sqm_mag': 21.3,
            },
          }),
        ),
        200,
      ),
      '/api/v1/public/calendar' => http.Response.bytes(
        utf8.encode(
          jsonEncode({
            'items': [
              {
                'kind': 'shower',
                'at': '2026-11-17T21:00:00Z',
                'all_day': true,
                'title': 'Leoniden-Maximum',
                'text': 'bis 15 pro Stunde',
              },
              {
                'kind': 'full_moon',
                'at': '2026-11-24T14:53:00Z',
                'all_day': true,
                'title': 'Vollmond',
                'text': 'Helle Nächte',
              },
            ],
          }),
        ),
        200,
      ),
      '/karte/suche' => http.Response.bytes(
        utf8.encode(
          jsonEncode([
            {
              'name': 'Linz, Oberösterreich, Österreich',
              'lat': 48.3,
              'lon': 14.29,
            },
          ]),
        ),
        200,
      ),
      _ => http.Response('', 404),
    };
  }),
);

void main() {
  testWidgets('asks for a place first, searches one and shows its sky', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(800, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final seen = <Uri>[];
    final places = MemoryPlaceStore();
    final reminders = _Reminders();
    await tester.pumpWidget(
      MaterialApp(
        home: SkyScreen(
          client: _hub(seen),
          places: places,
          location: _Here(),
          reminders: reminders,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('sky-choose')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('place-query')), 'Linz');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ListTile, 'Linz'));
    await tester.pumpAndSettle();

    expect(places.place?.place?.name, startsWith('Linz'));
    final asked = seen.firstWhere((u) => u.path == '/api/v1/public/sky');
    expect(asked.queryParameters, {
      'lat': '48.3',
      'lon': '14.3',
      'tz': 'Europe/Vienna',
    });
    expect(find.text('gut'), findsOneWidget);
    expect(find.text('mäßig'), findsOneWidget);
    expect(find.textContaining('GEMESSEN · 12 km'), findsOneWidget);
    expect(find.textContaining('Lanzenberg · 20 % Wolken'), findsOneWidget);
    expect(find.text('bei dir ab Kp 8'), findsOneWidget);
    expect(find.textContaining('Leoniden-Maximum'), findsOneWidget);

    await tester.tap(find.byKey(const Key('sky-calendar')));
    await tester.pumpAndSettle();
    expect(find.text('Vollmond'), findsOneWidget);
    await tester.tap(find.byKey(const Key('filter-shower')));
    await tester.pumpAndSettle();
    expect(find.text('Vollmond'), findsNothing);

    // The bell: remembered on the phone, tapped again forgotten.
    final bell = find.byIcon(Icons.notifications_none);
    await tester.tap(bell.first);
    await tester.pumpAndSettle();
    expect(reminders.ids, hasLength(1));
    expect(find.byIcon(Icons.notifications_active), findsOneWidget);
    await tester.tap(find.byIcon(Icons.notifications_active));
    await tester.pumpAndSettle();
    expect(reminders.ids, isEmpty);
  });

  testWidgets('the night table, and a refused location is explained', (
    tester,
  ) async {
    final here = _Here();
    await tester.pumpWidget(
      MaterialApp(
        home: SkyScreen(
          client: _hub([]),
          places: MemoryPlaceStore(const SkyPlace.here()),
          location: here,
          reminders: _Reminders(),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(here.asked, 1);
    expect(find.textContaining('Ohne Freigabe'), findsOneWidget);

    await tester.pumpWidget(
      MaterialApp(
        home: NightScreen(
          nights: [SkyNight.fromJson(_night('2026-10-28', 'good', 4))],
          initial: 0,
          place: 'Linz',
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.text('Klar und dunkel von 01:00 bis 05:00 Uhr.'),
      findsOneWidget,
    );
    expect(find.text('25'), findsNWidgets(2)); // wind per hour
  });

  test('reminders fire at 18:00 for all-day events, else 30 min before', () {
    CalendarItem item(String at, bool allDay) => CalendarItem.fromJson({
      'kind': 'shower',
      'at': at,
      'all_day': allDay,
      'title': 'x',
      'text': 'y',
    });
    final now = DateTime(2026, 10, 1);
    final day = reminderTime(item('2026-11-17T21:00:00Z', true), now: now)!;
    expect((day.hour, day.minute), (18, 0));
    final pass = item('2026-11-17T19:00:00Z', false);
    expect(
      reminderTime(pass, now: now),
      pass.at.toLocal().subtract(const Duration(minutes: 30)),
    );
    expect(reminderTime(pass, now: DateTime(2027)), isNull);
    expect(reminderId(pass), reminderId(item('2026-11-17T19:00:00Z', false)));
  });
}
