// Client for the hub's app API v1 (server/src/allskyhub_server/api/app.py).

import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

/// Default hub (SPEC §2); self-hosters enter their own URL at login.
const defaultHubUrl = 'https://allskyhub.org';

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
  });

  factory Night.fromJson(Map<String, dynamic> json) => Night(
    nightId: json['night_id'] as String,
    frames: json['frames'] as int,
    first: json['first'] == null
        ? null
        : DateTime.parse(json['first'] as String),
    last: json['last'] == null ? null : DateTime.parse(json['last'] as String),
    products: (json['products'] as int?) ?? 0,
  );

  /// `YYYYMMDD`: the evening the night started (SPEC §4.5).
  final String nightId;
  final int frames;

  /// Time of the first and last archived frame; null for a night with only products.
  final DateTime? first;
  final DateTime? last;

  /// Night products (keogram, startrails, timelapse) announced for this night.
  final int products;

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

  Future<dynamic> _send(String method, String path, {Object? body}) async {
    final request = http.Request(method, _uri(path))
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
  Future<bool> ensureProduct(
    String cameraId,
    String nightId,
    String name,
  ) async {
    final request = http.Request('GET', productUrl(cameraId, nightId, name))
      ..headers.addAll(authHeaders);
    final response = await _http.send(request);
    // Only the status matters; drop the body (it may be a long video).
    unawaited(response.stream.listen(null).cancel());
    if (response.statusCode == 200 || response.statusCode == 206) return true;
    if (response.statusCode == 202) return false;
    throw HubException(response.statusCode, response.reasonPhrase ?? 'error');
  }

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
