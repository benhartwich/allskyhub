// Client for the hub's app API v1 (server/src/allskyhub_server/api/app.py).

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'test_update.dart';

export 'test_update.dart' show TestBuild;

/// Default hub (SPEC §2); self-hosters enter their own URL at login.
const defaultHubUrl = 'https://allskyhub.org';

/// SPEC §6.4 Labels: why a detection is a false positive.
const labelReasons = {
  'aircraft': 'Flugzeug',
  'satellite': 'Satellit',
  'artifact': 'Bildfehler',
  'other': 'Sonstiges',
};

class HubException implements Exception {
  HubException(this.statusCode, this.message);

  final int statusCode;
  final String message;

  bool get unauthorized => statusCode == 401;

  @override
  String toString() => 'HubException($statusCode): $message';
}

/// A camera as the app API returns it.
class Camera {
  Camera({
    required this.id,
    required this.name,
    required this.profile,
    required this.online,
    this.lastSeenAt,
    this.latestImageAt,
    this.status = const {},
    this.frame = const {},
    this.role = 'owner',
    this.settingsSchema = const [],
  });

  factory Camera.fromJson(Map<String, dynamic> json) => Camera(
    id: json['id'] as String,
    name: json['name'] as String,
    profile: json['profile'] as String,
    online: json['online'] as bool,
    lastSeenAt: _date(json['last_seen_at']),
    latestImageAt: _date(json['latest_image_at']),
    status: (json['status'] as Map<String, dynamic>?) ?? const {},
    frame: (json['frame'] as Map<String, dynamic>?) ?? const {},
    role: json['role'] as String? ?? 'owner',
    settingsSchema: (json['settings_schema'] as List<dynamic>?) ?? const [],
  );

  final String id;
  final String name;
  final String profile;
  final bool online;
  final DateTime? lastSeenAt;
  final DateTime? latestImageAt;

  /// Latest `status` body (SPEC §6.3): mode, exposure_us, gain, sensor_temp_c, ...
  final Map<String, dynamic> status;

  /// Latest `frame` body (SPEC §4.4).
  final Map<String, dynamic> frame;

  /// Roadmap C10: `owner`, or `admin`/`viewer` for a camera shared with this account
  /// (older hubs send nothing: their cameras are all owned).
  final String role;

  /// SPEC §6.9: the form for the settings screen (`settings_schema.dart`); empty from
  /// hubs that do not send it.
  final List<dynamic> settingsSchema;

  bool get isOwner => role == 'owner';
  bool get canChange => role != 'viewer';

  static DateTime? _date(Object? value) =>
      value is String ? DateTime.parse(value) : null;
}

/// A night with archived frames and/or night products (hub app API `/nights`).
class Night {
  Night({
    required this.nightId,
    required this.frames,
    this.first,
    this.last,
    this.products = 0,
    this.events = 0,
    this.sky = 0,
  });

  factory Night.fromJson(Map<String, dynamic> json) => Night(
    nightId: json['night_id'] as String,
    frames: json['frames'] as int,
    first: json['first'] == null
        ? null
        : DateTime.parse(json['first'] as String),
    last: json['last'] == null ? null : DateTime.parse(json['last'] as String),
    products: (json['products'] as int?) ?? 0,
    events: (json['events'] as int?) ?? 0,
    sky: (json['sky'] as int?) ?? 0,
  );

  /// `YYYYMMDD`: the evening the night started (SPEC §4.5).
  final String nightId;
  final int frames;

  /// Time of the first and last archived frame; null for a night with only products.
  final DateTime? first;
  final DateTime? last;

  /// Night products (keogram, startrails, timelapse) announced for this night.
  final int products;

  /// Detections (SPEC §6.4) reported for this night.
  final int events;

  /// Sky measurements (SPEC §6.3 status.sky) for this night.
  final int sky;

  DateTime get evening => DateTime(
    int.parse(nightId.substring(0, 4)),
    int.parse(nightId.substring(4, 6)),
    int.parse(nightId.substring(6, 8)),
  );
}

/// A night product (SPEC §5.2). `pending`: the hub asked the camera for the full file.
class Product {
  Product({
    required this.kind,
    required this.name,
    required this.contentType,
    required this.size,
    required this.hasFull,
    required this.hasThumb,
    required this.pending,
    this.durationS,
  });

  factory Product.fromJson(Map<String, dynamic> json) => Product(
    kind: json['kind'] as String,
    name: json['name'] as String,
    contentType: json['content_type'] as String,
    size: json['size'] as int,
    durationS: (json['duration_s'] as num?)?.toDouble(),
    hasFull: json['has_full'] as bool,
    hasThumb: json['has_thumb'] as bool,
    pending: json['pending'] as bool,
  );

  /// `keogram`, `startrails` or `timelapse`.
  final String kind;
  final String name;
  final String contentType;
  final int size;
  final double? durationS;
  final bool hasFull;
  final bool hasThumb;
  final bool pending;

  bool get isVideo => contentType == 'video/mp4';
}

/// An archived frame (SPEC §4.4). Without `hasFull` only the thumbnail is left.
class FrameItem {
  FrameItem({
    required this.name,
    required this.capturedAt,
    required this.mode,
    required this.exposureUs,
    required this.gain,
    required this.sunElevation,
    required this.hasFull,
  });

  factory FrameItem.fromJson(Map<String, dynamic> json) => FrameItem(
    name: json['name'] as String,
    capturedAt: DateTime.parse(json['captured_at'] as String),
    mode: json['mode'] as String,
    exposureUs: json['exposure_us'] as int,
    gain: (json['gain'] as num).toDouble(),
    sunElevation: (json['sun_elevation'] as num).toDouble(),
    hasFull: json['has_full'] as bool,
  );

  final String name;
  final DateTime capturedAt;
  final String mode;
  final int exposureUs;
  final double gain;
  final double sunElevation;
  final bool hasFull;
}

/// One sky measurement (SPEC §6.3): null where the camera could not judge (day, dusk).
class SkySample {
  SkySample({required this.at, this.cloudCover, this.sqmMag, this.stars});

  factory SkySample.fromJson(Map<String, dynamic> json) => SkySample(
    at: DateTime.parse(json['at'] as String),
    cloudCover: (json['cloud_cover'] as num?)?.toDouble(),
    sqmMag: (json['sqm_mag'] as num?)?.toDouble(),
    stars: json['stars'] as int?,
  );

  final DateTime at;

  /// 0..1
  final double? cloudCover;

  /// mag/arcsec², approximate until calibrated.
  final double? sqmMag;
  final int? stars;
}

/// A detection reported by the camera (SPEC §6.4).
class SkyEvent {
  SkyEvent({
    required this.id,
    required this.nightId,
    required this.kind,
    required this.start,
    required this.end,
    required this.confidence,
    required this.hasImage,
    required this.hasThumb,
    required this.hasFull,
    this.data = const {},
    this.label,
    this.labelReason,
    this.labelAuto = false,
    this.labelDetail,
    this.imageRev = 1,
  });

  factory SkyEvent.fromJson(Map<String, dynamic> json) => SkyEvent(
    id: json['id'] as String,
    nightId: json['night_id'] as String,
    kind: json['kind'] as String,
    start: DateTime.parse(json['start'] as String),
    end: DateTime.parse(json['end'] as String),
    confidence: (json['confidence'] as num).toDouble(),
    hasImage: json['has_image'] as bool,
    hasThumb: json['has_thumb'] as bool,
    hasFull: json['has_full'] as bool,
    data: (json['data'] as Map<String, dynamic>?) ?? const {},
    label: json['label'] as String?,
    labelReason: json['label_reason'] as String?,
    labelAuto: json['label_auto'] as bool? ?? false,
    labelDetail: json['label_detail'] as String?,
    imageRev: (json['image_rev'] as int?) ?? 1,
  );

  final String id;
  final String nightId;

  /// meteor, lightning, aurora, nlc, satellite, clouds, sky_quality
  final String kind;
  final DateTime start;
  final DateTime end;
  final double confidence;
  final bool hasImage;
  final bool hasThumb;
  final bool hasFull;
  final Map<String, dynamic> data;

  /// The owner's verdict: "confirmed", "false_positive" or null.
  final String? label;

  /// Why it is a false positive (SPEC §6.4 Labels): aircraft, satellite, artifact, other.
  final String? labelReason;

  /// Set by a matcher (SPEC §7.8: an aircraft), not by a person; [labelDetail] names it.
  final bool labelAuto;
  final String? labelDetail;

  /// SPEC §6.4: revision of a replaceable picture (a growing aurora); part of image URLs.
  final int imageRev;

  /// An episode (aurora, NLC) that is still going on.
  bool get ongoing => data['ongoing'] == true;
}

class HubClient {
  HubClient({required this.baseUrl, http.Client? httpClient, this.token})
    : _http = httpClient ?? http.Client();

  final String baseUrl;
  final http.Client _http;
  String? token;

  Map<String, String> get authHeaders =>
      token == null ? const {} : {'Authorization': 'Bearer $token'};

  Uri _uri(String path, [Map<String, String>? query]) =>
      Uri.parse(baseUrl).replace(path: '/api/v1$path', queryParameters: query);

  Future<dynamic> _send(String method, String path, {Object? body}) =>
      _sendUri(method, _uri(path), body: body);

  Future<dynamic> _sendUri(String method, Uri uri, {Object? body}) async {
    final request = http.Request(method, uri)
      ..headers.addAll(authHeaders)
      ..headers['Accept'] = 'application/json';
    if (body != null) {
      request.headers['Content-Type'] = 'application/json';
      request.body = jsonEncode(body);
    }
    final response = await http.Response.fromStream(await _http.send(request));
    if (response.statusCode >= 400) {
      var message = response.reasonPhrase ?? 'error';
      try {
        message = (jsonDecode(response.body) as Map<String, dynamic>)['detail']
            .toString();
      } on FormatException {
        // Not JSON (e.g. a proxy error page): keep the reason phrase.
      }
      throw HubException(response.statusCode, message);
    }
    if (response.body.isEmpty) return null;
    return jsonDecode(utf8.decode(response.bodyBytes));
  }

  /// Signs in and keeps the token on this client; returns it for storage.
  Future<String> login(
    String email,
    String password, {
    String label = '',
  }) async {
    final json = await _send(
      'POST',
      '/auth/login',
      body: {'email': email, 'password': password, 'label': label},
    );
    token = (json as Map<String, dynamic>)['access_token'] as String;
    return token!;
  }

  Future<void> logout() async {
    await _send('POST', '/auth/logout');
    token = null;
  }

  /// Roadmap #10: needs the current password; signs out the web and every other app.
  /// SPEC §6.5 set_settings: only the given keys change; the hub waits for the camera's
  /// answer. Throws a [HubException] with a German message (400 rejected, 409 offline, 504).
  Future<void> updateCameraSettings(
    String cameraId,
    Map<String, Object?> changes,
  ) => _send('PUT', '/cameras/$cameraId/settings', body: changes);

  /// Retention of detections and the notifications (roadmap #6, #15, C1–C3).
  Future<AccountSettings> accountSettings() async => AccountSettings.fromJson(
    await _send('GET', '/account/settings') as Map<String, dynamic>,
  );

  /// Only the given keys change (e.g. `{'event_keep_days': 90}`).
  Future<AccountSettings> updateAccountSettings(
    Map<String, Object> changes,
  ) async => AccountSettings.fromJson(
    await _send('PUT', '/account/settings', body: changes)
        as Map<String, dynamic>,
  );

  Future<void> changePassword(String current, String newPassword) => _send(
    'POST',
    '/account/password',
    body: {'current': current, 'new': newPassword},
  );

  /// Roadmap #10: deletes the account, unpairs its cameras and removes their images.
  Future<void> deleteAccount(String password) async {
    await _send('POST', '/account/delete', body: {'password': password});
    token = null;
  }

  Future<List<Camera>> cameras() async {
    final json = await _send('GET', '/cameras') as List<dynamic>;
    return [for (final c in json) Camera.fromJson(c as Map<String, dynamic>)];
  }

  Future<Camera> camera(String id) async => Camera.fromJson(
    await _send('GET', '/cameras/$id') as Map<String, dynamic>,
  );

  /// SPEC §6.2 step 3: binds the camera with its pairing code to this account.
  Future<Camera> claim(String code, {String name = ''}) async =>
      Camera.fromJson(
        await _send(
              'POST',
              '/cameras/claim',
              body: {'code': code, 'name': name},
            )
            as Map<String, dynamic>,
      );

  Future<void> remove(String id) => _send('DELETE', '/cameras/$id');

  /// Roadmap C10: the accounts a camera is shared with (owner only).
  Future<List<Member>> members(String cameraId) async =>
      _members(await _send('GET', '/cameras/$cameraId/members'));

  /// Adds an existing account or changes its role (`viewer`, `admin`); throws a
  /// [HubException] with a German message for unknown addresses.
  Future<List<Member>> share(
    String cameraId,
    String email, {
    String role = 'viewer',
  }) async => _members(
    await _send(
      'POST',
      '/cameras/$cameraId/members',
      body: {'email': email, 'role': role},
    ),
  );

  /// The owner removes someone, or a member leaves with their own id ([myId]).
  Future<void> unshare(String cameraId, String userId) =>
      _send('DELETE', '/cameras/$cameraId/members/$userId');

  Future<String> myId() async =>
      (await _send('GET', '/account/me') as Map<String, dynamic>)['user_id']
          as String;

  static List<Member> _members(Object? json) => [
    for (final m in json! as List<dynamic>)
      Member.fromJson(m as Map<String, dynamic>),
  ];

  /// Audit L2: whether the camera takes part in the network of other owners' cameras
  /// (owner only).
  Future<bool> network(String cameraId) async =>
      (await _send('GET', '/cameras/$cameraId/network')
              as Map<String, dynamic>)['network']
          as bool;

  Future<bool> setNetwork(String cameraId, bool on) async =>
      (await _send('PUT', '/cameras/$cameraId/network', body: {'network': on})
              as Map<String, dynamic>)['network']
          as bool;

  /// Roadmap H5: the camera's Discord channel (owner only).
  Future<DiscordState> discord(String cameraId) async => DiscordState.fromJson(
    await _send('GET', '/cameras/$cameraId/discord') as Map<String, dynamic>,
  );

  /// An empty [webhook] keeps the stored address; a 400 carries a German sentence.
  Future<DiscordState> updateDiscord(
    String cameraId, {
    String webhook = '',
    required List<String> kinds,
    required bool summary,
  }) async => DiscordState.fromJson(
    await _send(
          'PUT',
          '/cameras/$cameraId/discord',
          body: {'webhook': webhook, 'kinds': kinds, 'summary': summary},
        )
        as Map<String, dynamic>,
  );

  Future<DiscordState> removeDiscord(String cameraId) async =>
      DiscordState.fromJson(
        await _send('DELETE', '/cameras/$cameraId/discord')
            as Map<String, dynamic>,
      );

  Future<void> testDiscord(String cameraId) =>
      _send('POST', '/cameras/$cameraId/discord/test');

  /// Roadmap #9, SPEC §6.8: the public sky page and what it shows (owner only).
  Future<PublicState> publicState(String cameraId) async =>
      PublicState.fromJson(
        await _send('GET', '/cameras/$cameraId/public/profile')
            as Map<String, dynamic>,
      );

  /// Switches the page ([enabled]) and/or replaces the whole [profile]; a 422 carries a
  /// German sentence.
  Future<PublicState> updatePublic(
    String cameraId, {
    bool? enabled,
    Map<String, dynamic>? profile,
    bool? onHome,
  }) async => PublicState.fromJson(
    await _send(
          'PUT',
          '/cameras/$cameraId/public/profile',
          body: {'enabled': ?enabled, 'profile': ?profile, 'on_home': ?onHome},
        )
        as Map<String, dynamic>,
  );

  /// The newest Android test build on the hub, or null (none, or an older hub).
  Future<TestBuild?> testBuild() async {
    final response = await _http.get(
      Uri.parse(baseUrl).replace(path: '/app/android/test.json'),
    );
    if (response.statusCode != 200) return null;
    return TestBuild.fromJson(
      jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>,
      baseUrl,
    );
  }

  /// Roadmap C11: the CSV files for science the hub offers.
  Future<List<ExportItem>> exports(String cameraId) async => [
    for (final e
        in await _send('GET', '/cameras/$cameraId/exports') as List<dynamic>)
      ExportItem.fromJson(e as Map<String, dynamic>),
  ];

  Future<Uint8List> exportCsv(String cameraId, String name) async {
    final response = await _http.get(
      _uri('/cameras/$cameraId/exports/$name.csv'),
      headers: authHeaders,
    );
    if (response.statusCode >= 400) {
      throw HubException(response.statusCode, response.reasonPhrase ?? 'error');
    }
    return response.bodyBytes;
  }

  /// Roadmap H4: the objects to choose from (planets, then Messier).
  Future<List<TargetItem>> targets() async => [
    for (final t in await _send('GET', '/targets') as List<dynamic>)
      TargetItem.fromJson(t as Map<String, dynamic>),
  ];

  /// Roadmap H4: is [key] clear now and in the next hours? 409: location unknown.
  Future<TargetPlan> target(String cameraId, String key) async =>
      TargetPlan.fromJson(
        await _sendUri('GET', _uri('/cameras/$cameraId/target', {'key': key}))
            as Map<String, dynamic>,
      );

  /// Roadmap C7: one year in months; [year] 0 is this year.
  Future<YearStats> stats(String cameraId, {int year = 0}) async =>
      YearStats.fromJson(
        await _sendUri(
              'GET',
              _uri('/cameras/$cameraId/stats', {if (year > 0) 'year': '$year'}),
            )
            as Map<String, dynamic>,
      );

  /// Nights with archived frames, newest first.
  Future<List<Night>> nights(String cameraId) async {
    final json =
        await _send('GET', '/cameras/$cameraId/nights') as List<dynamic>;
    return [for (final n in json) Night.fromJson(n as Map<String, dynamic>)];
  }

  /// Archived frames of one night, oldest first.
  Future<List<FrameItem>> frames(String cameraId, String nightId) async {
    final json =
        await _send('GET', '/cameras/$cameraId/nights/$nightId/frames')
            as List<dynamic>;
    return [
      for (final f in json) FrameItem.fromJson(f as Map<String, dynamic>),
    ];
  }

  Future<List<Product>> products(String cameraId, String nightId) async {
    final json =
        await _send('GET', '/cameras/$cameraId/nights/$nightId/products')
            as List<dynamic>;
    return [for (final p in json) Product.fromJson(p as Map<String, dynamic>)];
  }

  Uri productUrl(
    String cameraId,
    String nightId,
    String name, {
    bool thumb = false,
  }) => _uri(
    '/cameras/$cameraId/products/$nightId/$name',
    thumb ? {'variant': 'thumb'} : null,
  );

  /// Make sure the hub has the full product: true when it is there, false while the hub
  /// fetches it from the camera (202). Never downloads the file itself.
  Future<bool> ensureProduct(String cameraId, String nightId, String name) =>
      _ensure(productUrl(cameraId, nightId, name));

  /// Make sure the hub has a file it fetches from the camera on demand: true when it is
  /// there, false while the hub fetches it (202). Never downloads the file itself.
  Future<bool> _ensure(Uri url) async {
    final request = http.Request('GET', url)..headers.addAll(authHeaders);
    final response = await _http.send(request);
    // Only the status matters; drop the body (it may be a long video).
    unawaited(response.stream.listen(null).cancel());
    if (response.statusCode == 200 || response.statusCode == 206) return true;
    if (response.statusCode == 202) return false;
    throw HubException(response.statusCode, response.reasonPhrase ?? 'error');
  }

  /// Newest detections across nights; page with [before] (the last item's start).
  Future<List<SkyEvent>> events(
    String cameraId, {
    DateTime? before,
    int limit = 50,
    bool hideFalse = false,
  }) async {
    final query = {
      'limit': '$limit',
      if (hideFalse) 'hide_false': 'true',
      if (before != null) 'before': before.toUtc().toIso8601String(),
    };
    final uri = _uri('/cameras/$cameraId/events', query);
    final json = await _sendUri('GET', uri) as List<dynamic>;
    return [for (final e in json) SkyEvent.fromJson(e as Map<String, dynamic>)];
  }

  /// Health, "Heute Nacht" and last night (roadmap C1, C3, C14) for the camera screen.
  Future<Overview> overview(String cameraId) async => Overview.fromJson(
    await _send('GET', '/cameras/$cameraId/overview') as Map<String, dynamic>,
  );

  /// Measurements for the charts: one night (`YYYYMMDD`), the last [hours] (24, 72,
  /// 168, 720), or by default the newest night with sky data.
  Future<SeriesData> series(
    String cameraId, {
    String? night,
    int? hours,
  }) async {
    final query = {'night': ?night, if (hours != null) 'hours': '$hours'};
    final uri = _uri('/cameras/$cameraId/series', query.isEmpty ? null : query);
    return SeriesData.fromJson(
      await _sendUri('GET', uri) as Map<String, dynamic>,
    );
  }

  Future<List<SkySample>> nightSky(String cameraId, String nightId) async {
    final json =
        await _send('GET', '/cameras/$cameraId/nights/$nightId/sky')
            as List<dynamic>;
    return [
      for (final x in json) SkySample.fromJson(x as Map<String, dynamic>),
    ];
  }

  Future<List<SkyEvent>> nightEvents(String cameraId, String nightId) async {
    final json =
        await _send('GET', '/cameras/$cameraId/nights/$nightId/events')
            as List<dynamic>;
    return [for (final e in json) SkyEvent.fromJson(e as Map<String, dynamic>)];
  }

  Uri eventImageUrl(String cameraId, SkyEvent event, {bool thumb = false}) =>
      _uri('/cameras/$cameraId/events/${event.nightId}/${event.id}/image', {
        if (thumb) 'variant': 'thumb',
        'v': '${event.imageRev}',
      });

  /// The owner's verdict: "confirmed", "false_positive" ("kein Meteor") or null to clear.
  /// [reason] only with `false_positive` (see [labelReasons]).
  Future<SkyEvent> labelEvent(
    String cameraId,
    SkyEvent event,
    String? label, {
    String? reason,
  }) async => SkyEvent.fromJson(
    await _send(
          'PUT',
          '/cameras/$cameraId/events/${event.nightId}/${event.id}/label',
          body: {'label': label, 'reason': ?reason},
        )
        as Map<String, dynamic>,
  );

  Future<bool> ensureEventImage(String cameraId, SkyEvent event) =>
      _ensure(eventImageUrl(cameraId, event));

  Uri frameUrl(
    String cameraId,
    String nightId,
    String name, {
    bool thumb = false,
  }) => _uri(
    '/cameras/$cameraId/frames/$nightId/$name/${thumb ? 'thumb' : 'full'}.jpg',
  );

  /// URL of the latest image; `live` asks the hub for every frame (SPEC §6.5).
  Uri imageUrl(String id, {bool thumb = false, bool live = false, int? bust}) =>
      _uri('/cameras/$id/image/${thumb ? 'thumb' : 'full'}.jpg', {
        if (live) 'live': 'true',
        if (bust != null) 't': '$bust',
      });
}

/// Per-account settings from `GET /account/settings`. Notification switches the hub does
/// not know (older hub) are missing from [notify].
class AccountSettings {
  AccountSettings.fromJson(Map<String, dynamic> json)
    : keepDays = json['event_keep_days'] as int,
      pushAvailable = json['push_available'] as bool? ?? false,
      mailAvailable = json['mail_available'] as bool? ?? false,
      notify = {
        for (final key in notifyKeys)
          if (json[key] is bool) key: json[key] as bool,
      };

  static const notifyKeys = [
    'notify_events',
    'notify_offline',
    'notify_aurora',
    'notify_summary',
    'notify_tonight',
    'notify_fireball',
    'notify_by_mail',
  ];

  final int keepDays;
  final bool pushAvailable;
  final bool mailAvailable;
  final Map<String, bool> notify;
}

DateTime? _optionalDate(Object? value) =>
    value is String ? DateTime.parse(value) : null;

class HealthItem {
  HealthItem.fromJson(Map<String, dynamic> json)
    : level = json['level'] as String,
      text = json['text'] as String;

  final String level; // "warn" or "error"
  final String text;
}

class TonightHour {
  TonightHour.fromJson(Map<String, dynamic> json)
    : at = DateTime.parse(json['at'] as String),
      clouds = json['clouds'] as int?,
      moonUp = json['moon_up'] as bool,
      good = json['good'] as bool;

  final DateTime at;
  final int? clouds;
  final bool moonUp;
  final bool good;
}

class Tonight {
  Tonight.fromJson(Map<String, dynamic> json)
    : summary = json['summary'] as String,
      sunset = _optionalDate(json['sunset']),
      dusk = _optionalDate(json['dusk']),
      dawn = _optionalDate(json['dawn']),
      sunrise = _optionalDate(json['sunrise']),
      moonrise = _optionalDate(json['moonrise']),
      moonset = _optionalDate(json['moonset']),
      moonIllumination = (json['moon_illumination'] as num).toDouble(),
      moonPhase = json['moon_phase'] as String,
      hours = [
        for (final h in json['hours'] as List<dynamic>)
          TonightHour.fromJson(h as Map<String, dynamic>),
      ];

  final String summary;
  final DateTime? sunset, dusk, dawn, sunrise, moonrise, moonset;
  final double moonIllumination;
  final String moonPhase;
  final List<TonightHour> hours;
}

class Overview {
  Overview.fromJson(Map<String, dynamic> json)
    : online = json['online'] as bool,
      health = [
        for (final h in json['health'] as List<dynamic>)
          HealthItem.fromJson(h as Map<String, dynamic>),
      ],
      tonight = json['tonight'] == null
          ? null
          : Tonight.fromJson(json['tonight'] as Map<String, dynamic>),
      lastNight =
          (json['last_night'] as Map<String, dynamic>?)?['line'] as String?,
      lastNightId =
          (json['last_night'] as Map<String, dynamic>?)?['night_id'] as String?;

  final bool online;
  final List<HealthItem> health;
  final Tonight? tonight;
  final String? lastNight;
  final String? lastNightId;
}

/// One table of `GET /cameras/<id>/series`: times (Unix seconds) and value columns;
/// `null` is a gap (no measurement), never zero.
class SeriesTable {
  SeriesTable.fromJson(Map<String, dynamic> json)
    : t = [for (final v in json['t'] as List<dynamic>) (v as num).toDouble()],
      columns = {
        for (final e in json.entries)
          if (e.key != 't' && e.value is List)
            e.key: [
              for (final v in e.value as List<dynamic>) (v as num?)?.toDouble(),
            ],
      };

  final List<double> t;
  final Map<String, List<double?>> columns;
}

class SeriesData {
  SeriesData.fromJson(Map<String, dynamic> json)
    : from = (json['from'] as num).toDouble(),
      to = (json['to'] as num).toDouble(),
      tz = json['tz'] as String,
      night = json['night'] as String?,
      hours = json['hours'] as int?,
      bucketSeconds = json['bucket_s'] as int? ?? 0,
      sky = SeriesTable.fromJson(json['sky'] as Map<String, dynamic>),
      status = json['status'] == null
          ? null
          : SeriesTable.fromJson(json['status'] as Map<String, dynamic>),
      ephemeris = json['ephemeris'] == null
          ? null
          : SeriesTable.fromJson(json['ephemeris'] as Map<String, dynamic>);

  final double from, to;
  final String tz;
  final String? night;
  final int? hours;
  final int bucketSeconds;
  final SeriesTable sky;
  final SeriesTable? status;
  final SeriesTable? ephemeris;

  double? get moonIllumination =>
      ephemeris?.columns['moon_illumination']?.firstOrNull;
}

/// An account a camera is shared with (roadmap C10).
class Member {
  Member.fromJson(Map<String, dynamic> json)
    : userId = json['user_id'] as String,
      email = json['email'] as String,
      role = json['role'] as String;

  final String userId;
  final String email;

  /// `viewer` (looks) or `admin` (also changes settings, marks detections).
  final String role;
}

/// `GET /cameras/{id}/public/profile`: the same model as the camera's local UI (SPEC §6.8).
class PublicState {
  PublicState.fromJson(Map<String, dynamic> json)
    : enabled = json['enabled'] as bool,
      url = json['url'] as String?,
      profile = json['profile'] as Map<String, dynamic>,
      onHome = json['on_home'] as bool? ?? false;

  final bool enabled;
  final String? url;

  /// Also listed on the hub's start page (the hub's own switch, not in the profile).
  final bool onHome;

  /// title, location_label, show_coordinates, camera, lens, computer, owner, notes and
  /// `sections` (live, loop, archive, events, sky, aurora, observatory). Sent back whole
  /// on save, so fields a newer hub adds survive.
  final Map<String, dynamic> profile;
}

/// One month or a whole year of `GET /cameras/{id}/stats` (roadmap C7).
class StatsPeriod {
  StatsPeriod.fromJson(Map<String, dynamic> json)
    : label = json['label'] as String,
      nights = json['nights'] as int,
      clearNights = json['clear_nights'] as int,
      clearHours = (json['clear_hours'] as num).toDouble(),
      bestSqm = (json['best_sqm'] as num?)?.toDouble(),
      meteors = json['meteors'] as int,
      aurora = json['aurora'] as int,
      storms = json['storms'] as int;

  final String label;

  /// Nights with any data; clear ones have at least two clear hours.
  final int nights;
  final int clearNights;
  final double clearHours;
  final double? bestSqm;
  final int meteors;
  final int aurora;
  final int storms;
}

class YearStats {
  YearStats.fromJson(Map<String, dynamic> json)
    : year = json['year'] as int,
      months = [
        for (final m in json['months'] as List<dynamic>)
          StatsPeriod.fromJson(m as Map<String, dynamic>),
      ],
      total = StatsPeriod.fromJson(json['total'] as Map<String, dynamic>),
      showers = {
        for (final e in (json['showers'] as Map<String, dynamic>).entries)
          e.key: e.value as int,
      },
      years = [for (final y in json['years'] as List<dynamic>) y as int],
      sqmTrendText = json['sqm_trend_text'] as String? ?? '',
      sqmMonths = [
        for (final m in json['sqm_months'] as List<dynamic>? ?? const [])
          (
            label: (m as List<dynamic>)[0] as String,
            sqm: (m[1] as num).toDouble(),
          ),
      ];

  final int year;
  final List<StatsPeriod> months;
  final StatsPeriod total;

  /// Meteor showers by name, most meteors first.
  final Map<String, int> showers;

  /// Years with data, newest first.
  final List<int> years;

  /// Roadmap N5: how the sky brightness changes over the years ('' without data), and
  /// the monthly values behind it (mag/″², higher is darker).
  final String sqmTrendText;
  final List<({String label, double sqm})> sqmMonths;
}

/// A CSV export (roadmap C11), e.g. `meteors` → `meteors.csv`.
class ExportItem {
  ExportItem.fromJson(Map<String, dynamic> json)
    : name = json['name'] as String,
      title = json['title'] as String,
      filename = json['filename'] as String;

  final String name;
  final String title;
  final String filename;
}

/// `GET /cameras/{id}/discord` (roadmap H5); [webhook] without its secret part.
class DiscordState {
  DiscordState.fromJson(Map<String, dynamic> json)
    : configured = json['configured'] as bool,
      webhook = json['webhook'] as String?,
      kinds = [for (final k in json['kinds'] as List<dynamic>) k as String],
      summary = json['summary'] as bool,
      broken = json['broken'] as bool,
      available = {
        for (final e in (json['available'] as Map<String, dynamic>).entries)
          e.key: e.value as String,
      };

  final bool configured;
  final String? webhook;
  final List<String> kinds;
  final bool summary;

  /// Discord no longer knows the webhook: the owner enters a new one.
  final bool broken;

  /// Every kind that can go to the channel, with its German label.
  final Map<String, String> available;
}

/// An object for "Ziel frei?" (roadmap H4), e.g. `M31` or `jupiter`.
class TargetItem {
  TargetItem.fromJson(Map<String, dynamic> json)
    : key = json['key'] as String,
      label = json['label'] as String,
      kind = json['kind'] as String;

  final String key;
  final String label;

  /// `planet`, or the Messier type (`gx`, `gc`, `oc`, …).
  final String kind;
}

class TargetHourItem {
  TargetHourItem.fromJson(Map<String, dynamic> json)
    : at = DateTime.parse(json['at'] as String),
      alt = (json['alt'] as num).toDouble(),
      direction = json['direction'] as String,
      clouds = json['clouds'] as int?,
      moonNear = json['moon_near'] as bool,
      verdict = json['verdict'] as String,
      verdictText = json['verdict_text'] as String;

  final DateTime at;
  final double alt;
  final String direction;
  final int? clouds;
  final bool moonNear;

  /// `clear`, `partly`, `cloudy`, `low` or `bright`.
  final String verdict;
  final String verdictText;
}

class TargetPlan {
  TargetPlan.fromJson(Map<String, dynamic> json)
    : label = json['label'] as String,
      summary = json['summary'] as String,
      alt = (json['alt'] as num).toDouble(),
      direction = json['direction'] as String,
      cloudCover = (json['cloud_cover'] as num?)?.toDouble(),
      cloudSource = json['cloud_source'] as String? ?? '',
      verdict = json['verdict'] as String,
      verdictText = json['verdict_text'] as String,
      hours = [
        for (final h in json['hours'] as List<dynamic>)
          TargetHourItem.fromJson(h as Map<String, dynamic>),
      ];

  final String label;
  final String summary;
  final double alt;
  final String direction;

  /// What the camera sees now (0..1), or null without a fresh value.
  final double? cloudCover;

  /// "in Richtung des Objekts" (cloud map) or "ganzer Himmel"; empty from older hubs.
  final String cloudSource;
  final String verdict;
  final String verdictText;
  final List<TargetHourItem> hours;
}
