// Client for the hub's app API v1 (server/src/allskyhub_server/api/app.py).

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

  /// URL of the latest image; `live` asks the hub for every frame (SPEC §6.5).
  Uri imageUrl(String id, {bool thumb = false, bool live = false, int? bust}) =>
      _uri('/cameras/$id/image/${thumb ? 'thumb' : 'full'}.jpg', {
        if (live) 'live': 'true',
        if (bust != null) 't': '$bust',
      });
}
