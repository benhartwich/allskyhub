// Store screenshots of the real screens with sample data (Benjamin, 2026-10-16: "virtuelle
// Screenshots"). The sky pictures are synthetic (tools/store_sky.py), the rest is the app as
// it is. Off in normal test runs; to make them:
//
//   flutter test test/store --update-goldens --dart-define=STORE_SCREENSHOTS=true
//
// They land in test/store/shots/; copy them to store/play/screenshots/ for the listing.
// With --dart-define=STORE_LANG=en the app speaks English and they land in
// test/store/shots-en/, for store/play/en-US/screenshots/.
// The fonts come from the Flutter SDK (Roboto, Material icons) and, for symbols Roboto
// lacks, DejaVu Sans where the system has it (the flutter container does).

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:allskyhub_app/api/hub_client.dart';
import 'package:allskyhub_app/design.dart';
import 'package:allskyhub_app/l10n.dart';
import 'package:allskyhub_app/screens/camera_screen.dart';
import 'package:allskyhub_app/screens/cameras_screen.dart';
import 'package:allskyhub_app/screens/event_screens.dart';
import 'package:allskyhub_app/screens/gallery_screens.dart';
import 'package:allskyhub_app/screens/measurements_screen.dart';
import 'package:allskyhub_app/screens/sky_view_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _run = bool.fromEnvironment('STORE_SCREENSHOTS');
const _english = String.fromEnvironment('STORE_LANG') == 'en';
const _size = Size(1080, 2160); // 2:1, the longest Google Play takes
const _ratio = 3.0;
final _here = Directory('test/store');

Map<String, dynamic> _camera(String id, String name, {bool owner = true}) => {
  'id': id,
  'name': name,
  'profile': 'zwo-asi678mc',
  'agent_version': '0.1.3',
  'online': true,
  'paired_at': '2026-09-20T18:00:00Z',
  // Relative to the clock of the run, so the screens say "gerade eben".
  'last_seen_at': DateTime.now()
      .toUtc()
      .subtract(const Duration(seconds: 20))
      .toIso8601String(),
  'latest_image_at': DateTime.now()
      .toUtc()
      .subtract(const Duration(seconds: 30))
      .toIso8601String(),
  'role': owner ? 'owner' : 'viewer',
  'status': {
    'mode': 'night',
    'exposure_us': 30000000,
    'gain': 180,
    'sensor_temp_c': 9.4,
    'cpu_temp_c': 41.2,
    'disk_free_pct': 72.0,
    'uptime_s': 12 * 86400,
    'time_trusted': true,
    'orientation': {
      'north_deg': 3.1,
      'mirrored': false,
      'stars': 41,
      'rms_deg': 0.2,
    },
    'update': {'state': 'up_to_date', 'version': '0.1.3'},
  },
  'frame': {'sun_elevation': -38.4},
};

final _cameras = [
  _camera('sternwarte0000000000000000', 'Sternwarte'),
  _camera('garten00000000000000000000', 'Garten am See', owner: false),
];

Map<String, dynamic> _meteor(String id, String start, String shower) => {
  'id': id,
  'night_id': '20261014',
  'kind': 'meteor',
  'start': start,
  'end': start.replaceFirst(':12Z', ':13Z'),
  'confidence': 0.94,
  'has_image': true,
  'has_thumb': true,
  'has_full': true,
  'data': {
    'length_px': 438,
    'peak': 0.97,
    'frames': 2,
    'direction_deg': 216.0,
    'shower': shower,
    'shower_match': 'radiant',
    'start_altitude_deg': 62.0,
    'start_azimuth_deg': 70.0,
    'end_altitude_deg': 38.0,
    'end_azimuth_deg': 30.0,
  },
};

final _overview = {
  'online': true,
  'health': <Object>[],
  'tonight': {
    'summary': 'Klar und dunkel von 20:10 bis 05:40 Uhr.',
    'sunset': '2026-10-14T16:31:00Z',
    'dusk': '2026-10-14T18:10:00Z',
    'dawn': '2026-10-15T03:43:00Z',
    'sunrise': '2026-10-15T05:23:00Z',
    'moonrise': '2026-10-15T03:58:00Z',
    'moonset': null,
    'moon_illumination': 0.08,
    'moon_phase': 'abnehmende Sichel',
    'best_start': '2026-10-14T18:10:00Z',
    'best_end': '2026-10-15T03:40:00Z',
    'hours': [
      for (var h = 0; h < 10; h++)
        {
          'at': DateTime.utc(2026, 10, 14, 18 + h).toIso8601String(),
          'clouds': h == 7 ? 20 : 5,
          'moon_up': h >= 9,
          'good': true,
        },
    ],
  },
  'nowcast': {
    'cover': 0.04,
    'summary': 'Bleibt klar.',
    'wind': 'Wind aus Nordost, 8 km/h',
    'steps': <Object>[],
  },
  'last_night': {
    'night_id': '20261013',
    'line': '7 Meteore, 9 h klar',
    'meteors': 7,
    'clear_hours': 9.0,
    'best_sqm': 21.4,
  },
};

const _products = [
  {
    'kind': 'keogram',
    'name': 'keogram.jpg',
    'content_type': 'image/jpeg',
    'size': 412000,
    'duration_s': null,
    'has_full': true,
    'has_thumb': true,
    'pending': false,
  },
  {
    'kind': 'startrails',
    'name': 'startrails.jpg',
    'content_type': 'image/jpeg',
    'size': 1830000,
    'duration_s': null,
    'has_full': true,
    'has_thumb': true,
    'pending': false,
  },
  {
    'kind': 'timelapse',
    'name': 'timelapse.mp4',
    'content_type': 'video/mp4',
    'size': 96 * 1024 * 1024,
    'duration_s': 64.0,
    'has_full': true,
    'has_thumb': true,
    'pending': false,
  },
];

Map<String, dynamic> _series() {
  // 2026-10-14 12:00 UTC to the next noon, the night from 18:00 to 05:00 UTC.
  final from = DateTime.utc(2026, 10, 14, 12).millisecondsSinceEpoch / 1000;
  final t = <double>[];
  final sqm = <double?>[];
  final cloud = <double?>[];
  final stars = <double?>[];
  for (var i = 0; i <= 66; i++) {
    final ts = from + 6 * 3600 + i * 600;
    final x = i / 66;
    t.add(ts);
    sqm.add(18.6 + 2.8 * math.sin(math.pi * x) - (i > 60 ? (i - 60) * 0.4 : 0));
    cloud.add(i > 40 && i < 46 ? 25.0 : 4.0 + 2 * math.sin(i / 4));
    stars.add(380 + 260 * math.sin(math.pi * x) - (i > 40 && i < 46 ? 160 : 0));
  }
  final et = [for (var i = 0; i <= 288; i++) from + i * 300];
  return {
    'from': from,
    'to': from + 86400,
    'tz': 'Europe/Berlin',
    'night': '20261014',
    'hours': null,
    'bucket_s': 0,
    'sky': {'t': t, 'sqm': sqm, 'cloud': cloud, 'stars': stars},
    'status': {
      't': t,
      'exposure': [for (final _ in t) 30.0],
      'gain': [for (final _ in t) 180.0],
      'sensor': [for (var i = 0; i < t.length; i++) 12.0 - i * 0.06],
      'cpu': [for (final _ in t) 41.0],
      'disk': [for (final _ in t) 72.0],
    },
    'ephemeris': {
      't': et,
      'sun': [
        for (var i = 0; i <= 288; i++)
          -12 + 46 * math.cos((i / 288 - 0.02) * 2 * math.pi),
      ],
      'moon': [
        for (var i = 0; i <= 288; i++)
          -20 + 45 * math.cos((i / 288 - 0.68) * 2 * math.pi),
      ],
      'moon_illumination': [0.08],
    },
  };
}

/// The hub as far as these screens ask it; pictures come through [_Pictures].
HubClient _hub() => HubClient(
  baseUrl: 'https://hub.example',
  token: 'tok',
  httpClient: MockClient((request) async {
    final path = request.url.path.replaceFirst('/api/v1', '');
    Object? body;
    if (path == '/cameras') {
      body = _cameras;
    } else if (RegExp(r'^/cameras/[a-z0-9]+$').hasMatch(path)) {
      body = _cameras.firstWhere((c) => path.endsWith(c['id'] as String));
    } else if (path.endsWith('/overview')) {
      body = _overview;
    } else if (path.endsWith('/nights')) {
      body = [
        {
          'night_id': '20261014',
          'frames': 642,
          'first': '2026-10-14T17:05:00Z',
          'last': '2026-10-15T04:40:00Z',
          'products': 3,
          'events': 6,
          'sky': 1,
        },
        {
          'night_id': '20261013',
          'frames': 655,
          'first': '2026-10-13T17:03:00Z',
          'last': '2026-10-14T04:42:00Z',
          'products': 3,
          'events': 7,
          'sky': 1,
        },
      ];
    } else if (path.endsWith('/products')) {
      body = _products;
    } else if (path.endsWith('/frames')) {
      body = [
        for (var i = 0; i < 12; i++)
          {
            'name':
                'image-${20261014 + (i >= 6 ? 1 : 0)}${((18 + i) % 24).toString().padLeft(2, '0')}0000.jpg',
            'captured_at': DateTime.utc(2026, 10, 14, 18 + i).toIso8601String(),
            'mode': 'night',
            'exposure_us': 30000000,
            'gain': 180,
            'sun_elevation': -20.0 - i,
            'has_full': true,
          },
      ];
    } else if (path.contains('/events')) {
      body = [
        _meteor('meteor-20261015T001412Z', '2026-10-15T00:14:12Z', 'Orionids'),
        _meteor('meteor-20261014T223012Z', '2026-10-14T22:30:12Z', ''),
      ];
    } else if (path.endsWith('/sky-view')) {
      body = jsonDecode(File('${_here.path}/sky_view.json').readAsStringSync());
    } else if (path.endsWith('/series')) {
      body = _series();
    } else if (path.endsWith('/sky')) {
      body = <Object>[];
    } else if (path.endsWith('/test-build')) {
      return http.Response('', 404);
    }
    if (body == null) {
      // ignore: avoid_print
      print('store screenshots: no answer for ${request.method} $path');
      return http.Response('{"detail": "nicht da"}', 404);
    }
    return http.Response.bytes(
      utf8.encode(jsonEncode(body)),
      200,
      headers: {'content-type': 'application/json; charset=utf-8'},
    );
  }),
);

/// Pictures for Image.network: which synthetic sky a URL gets.
List<int> _picture(Uri url) {
  final path = url.toString();
  String name;
  if (path.contains('keogram')) {
    name = 'keogram';
  } else if (path.contains('startrails') || path.contains('timelapse')) {
    name = 'startrails';
  } else if (path.contains('/events/')) {
    name = path.contains('thumb') ? 'meteor_thumb' : 'meteor';
  } else {
    name = path.contains('thumb') ? 'live_thumb' : 'live';
  }
  return File('${_here.path}/sky/$name.jpg').readAsBytesSync();
}

class _Pictures extends HttpOverrides {
  @override
  HttpClient createHttpClient(SecurityContext? context) => _Client();
}

class _Client implements HttpClient {
  @override
  bool autoUncompress = false;

  @override
  Future<HttpClientRequest> getUrl(Uri url) async => _Request(url);

  @override
  Future<HttpClientRequest> openUrl(String method, Uri url) async =>
      _Request(url);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _Request implements HttpClientRequest {
  _Request(this.url);

  final Uri url;

  @override
  final HttpHeaders headers = _Headers();

  @override
  Future<HttpClientResponse> close() async => _Response(_picture(url));

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _Headers implements HttpHeaders {
  @override
  void add(String name, Object value, {bool preserveHeaderCase = false}) {}

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _Response extends Stream<List<int>> implements HttpClientResponse {
  _Response(this.bytes);

  final List<int> bytes;

  @override
  int get statusCode => 200;

  @override
  int get contentLength => bytes.length;

  @override
  HttpClientResponseCompressionState get compressionState =>
      HttpClientResponseCompressionState.notCompressed;

  @override
  StreamSubscription<List<int>> listen(
    void Function(List<int> event)? onData, {
    Function? onError,
    void Function()? onDone,
    bool? cancelOnError,
  }) => Stream<List<int>>.value(bytes).listen(
    onData,
    onError: onError,
    onDone: onDone,
    cancelOnError: cancelOnError,
  );

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Future<void> _fonts() async {
  final root = Platform.environment['FLUTTER_ROOT']!;
  final material = '$root/bin/cache/artifacts/material_fonts';
  Future<ByteData> file(String path) async =>
      ByteData.sublistView(await File(path).readAsBytes());
  final roboto = FontLoader('Roboto');
  for (final w in ['Regular', 'Medium', 'Bold', 'Light']) {
    roboto.addFont(file('$material/Roboto-$w.ttf'));
  }
  await roboto.load();
  // Symbols Roboto lacks (●, ↑), which a phone takes from its fallback fonts.
  final dejavu = File('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf');
  if (dejavu.existsSync()) {
    await (FontLoader('Symbols')..addFont(file(dejavu.path))).load();
  }
  await (FontLoader(
    'MaterialIcons',
  )..addFont(file('$material/MaterialIcons-Regular.otf'))).load();
  await (FontLoader(
    Sky.display,
  )..addFont(file('assets/fonts/SpaceGrotesk.ttf'))).load();
}

/// Where the phone looks: azimuth and altitude in degrees.
Pose _looking(double azDeg, double altDeg) {
  final a = azDeg * math.pi / 180, h = altDeg * math.pi / 180;
  final f = [math.cos(h) * math.sin(a), math.cos(h) * math.cos(a), math.sin(h)];
  final u = [
    -math.sin(h) * math.sin(a),
    -math.sin(h) * math.cos(a),
    math.cos(h),
  ];
  final x = [
    f[1] * u[2] - f[2] * u[1],
    f[2] * u[0] - f[0] * u[2],
    f[0] * u[1] - f[1] * u[0],
  ];
  Vec axis(int i) => (x: x[i], y: u[i], z: -f[i]);
  return Pose(axis(0), axis(1), axis(2));
}

class _Fixed implements PoseSource {
  _Fixed(this.pose);

  final Pose pose;

  @override
  Stream<Pose> get poses => Stream.value(pose);
}

/// The theme with Roboto named where it says no family: a phone fills in its own font
/// (Roboto on Android), a test draws boxes.
ThemeData _theme() {
  final theme = skyTheme();
  ButtonStyle? roboto(ButtonStyle? style) {
    final text = style?.textStyle;
    if (style == null || text == null) return style;
    return style.copyWith(
      textStyle: WidgetStateProperty.resolveWith(
        (states) => text.resolve(states)?.copyWith(fontFamily: 'Roboto'),
      ),
    );
  }

  return theme.copyWith(
    textTheme: theme.textTheme.apply(fontFamilyFallback: const ['Symbols']),
    listTileTheme: theme.listTileTheme.copyWith(
      subtitleTextStyle: theme.listTileTheme.subtitleTextStyle?.copyWith(
        fontFamily: 'Roboto',
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: roboto(theme.filledButtonTheme.style),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: roboto(theme.elevatedButtonTheme.style),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: roboto(theme.outlinedButtonTheme.style),
    ),
    textButtonTheme: TextButtonThemeData(
      style: roboto(theme.textButtonTheme.style),
    ),
  );
}

Future<void> _shot(
  WidgetTester tester,
  String name,
  Widget home, {
  Future<void> Function()? then,
}) async {
  tester.view.physicalSize = _size;
  tester.view.devicePixelRatio = _ratio;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    MaterialApp(debugShowCheckedModeBanner: false, theme: _theme(), home: home),
  );
  Future<void> settle() async {
    for (var i = 0; i < 6; i++) {
      await tester.runAsync(
        () => Future<void>.delayed(const Duration(milliseconds: 150)),
      );
      await tester.pump(const Duration(milliseconds: 300));
    }
  }

  await settle();
  if (then != null) {
    await then();
    await settle();
  }
  // Decode every picture on screen before the shot.
  for (final element in find.byType(Image).evaluate()) {
    final image = element.widget as Image;
    await tester.runAsync(() => precacheImage(image.image, element));
  }
  await tester.pump(const Duration(milliseconds: 300));
  await expectLater(
    find.byType(MaterialApp),
    matchesGoldenFile('${_english ? 'shots-en' : 'shots'}/$name.png'),
  );
  await tester.pumpWidget(const SizedBox()); // stops timers
  await tester.pump(const Duration(seconds: 5));
}

void main() {
  setUpAll(() async {
    if (!_run) return;
    appEnglish = _english;
    HttpOverrides.global = _Pictures();
    await _fonts();
  });

  final camera = Camera.fromJson(_cameras.first);

  testWidgets('01 cameras', skip: !_run, (tester) async {
    await _shot(
      tester,
      '01-kameras',
      CamerasScreen(client: _hub(), onSignOut: () async {}),
    );
  });

  testWidgets('02 live', skip: !_run, (tester) async {
    await _shot(
      tester,
      '02-live',
      CameraScreen(client: _hub(), camera: camera),
    );
  });

  testWidgets('03 night', skip: !_run, (tester) async {
    await _shot(
      tester,
      '03-nacht',
      NightsScreen(client: _hub(), camera: camera),
      then: () async {
        await tester.tap(find.textContaining('642').first);
      },
    );
  });

  testWidgets('04 meteor', skip: !_run, (tester) async {
    final event = SkyEvent.fromJson(
      _meteor('meteor-20261015T001412Z', '2026-10-15T00:14:12Z', 'Orionids'),
    );
    await _shot(
      tester,
      '04-meteor',
      EventScreen(client: _hub(), cameraId: camera.id, event: event),
    );
  });

  testWidgets('05 sky view', skip: !_run, (tester) async {
    await _shot(
      tester,
      '05-himmel',
      SkyViewScreen(
        client: _hub(),
        camera: camera,
        poseSource: _Fixed(_looking(75, 28)),
        useCamera: false,
      ),
    );
  });

  testWidgets('06 measurements', skip: !_run, (tester) async {
    await _shot(
      tester,
      '06-messdaten',
      MeasurementsScreen(
        client: _hub(),
        cameraId: camera.id,
        night: '20261014',
      ),
    );
  });
}
