// The camera's local setup API (SPEC §7), reached over the LAN or its setup hotspot.

import 'dart:convert';

import 'package:http/http.dart' as http;

/// Default port of the agent's local web server.
const cameraSetupPort = 8080;

/// The camera in its own setup network `allskyhub-XXXX` (SPEC §7.1).
final setupModeUri = Uri.parse('http://10.42.0.1:$cameraSetupPort');

/// `GET /api/setup` of the camera.
class SetupInfo {
  SetupInfo({
    required this.deviceId,
    required this.hubUrl,
    required this.paired,
    this.pairingCode,
    this.expiresIn,
    this.profile = '',
    this.agentVersion = '',
    this.setupMode = false,
    this.lastError,
  });

  factory SetupInfo.fromJson(Map<String, dynamic> json) => SetupInfo(
    deviceId: json['device_id'] as String,
    hubUrl: json['hub_url'] as String,
    paired: json['paired'] as bool,
    pairingCode: json['pairing_code'] as String?,
    expiresIn: json['expires_in'] as int?,
    profile: (json['profile'] as String?) ?? '',
    agentVersion: (json['agent_version'] as String?) ?? '',
    setupMode: (json['setup_mode'] as bool?) ?? false,
    lastError: json['last_error'] as String?,
  );

  final String deviceId;
  final String hubUrl;
  final bool paired;

  /// Raw 6-character code (SPEC §6.2), null once paired or before the first registration.
  final String? pairingCode;
  final int? expiresIn;
  final String profile;
  final String agentVersion;

  /// The camera is in its own setup network (SPEC §7.1).
  final bool setupMode;

  /// Why the last attempt to join a network failed: `wifi_auth`, `wifi_not_found`,
  /// `no_internet`, or null.
  final String? lastError;

  /// Network name of the setup mode: `allskyhub-` and the id's first four characters.
  String get setupSsid => 'allskyhub-${deviceId.substring(0, 4).toUpperCase()}';
}

/// One entry of `GET /api/wifi/networks` (SPEC §7.1).
class WifiNetwork {
  WifiNetwork({required this.ssid, required this.signal, required this.secure});

  factory WifiNetwork.fromJson(Map<String, dynamic> json) => WifiNetwork(
    ssid: json['ssid'] as String,
    signal: json['signal'] as int,
    secure: json['secure'] as bool,
  );

  final String ssid;

  /// 0..100
  final int signal;
  final bool secure;
}

/// German text for `SetupInfo.lastError`.
String? describeSetupError(String? code) => switch (code) {
  null => null,
  'wifi_auth' =>
    'Die Kamera konnte sich nicht anmelden. Ist das WLAN-Passwort richtig?',
  'wifi_not_found' =>
    'Die Kamera hat das WLAN nicht gefunden. Steht sie in Reichweite?',
  'no_internet' => 'Die Kamera ist im WLAN, kommt aber nicht ins Internet.',
  _ => 'Die Kamera konnte sich nicht verbinden ($code).',
};

/// The address answers, but not as a set-up allskyhub camera (404 on `/api/setup`: another
/// device, or an agent without a configured hub).
class NotACameraException implements Exception {}

/// Turns user input like "192.168.1.50", "kamera.local:8080" or a full URL into the
/// setup API's base URI.
Uri cameraBaseUri(String input) {
  var text = input.trim();
  if (!text.contains('://')) text = 'http://$text';
  final uri = Uri.parse(text);
  return Uri(
    scheme: uri.scheme,
    host: uri.host,
    port: uri.hasPort ? uri.port : cameraSetupPort,
  );
}

class CameraSetupClient {
  CameraSetupClient(this.baseUri, {http.Client? httpClient})
    : _http = httpClient ?? http.Client();

  final Uri baseUri;
  final http.Client _http;

  Future<List<WifiNetwork>> networks() async {
    final response = await _http
        .get(baseUri.replace(path: '/api/wifi/networks'))
        .timeout(const Duration(seconds: 15));
    if (response.statusCode != 200) {
      throw http.ClientException('networks answered ${response.statusCode}');
    }
    final json = jsonDecode(utf8.decode(response.bodyBytes)) as List<dynamic>;
    return [
      for (final n in json) WifiNetwork.fromJson(n as Map<String, dynamic>),
    ];
  }

  /// SPEC §7.1: the camera answers 202 and then leaves its setup network.
  Future<void> sendNetwork({
    required String ssid,
    String? password,
    required String country,
    String? hubUrl,
  }) async {
    final response = await _http
        .post(
          baseUri.replace(path: '/api/setup/network'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({
            'ssid': ssid,
            if (password != null && password.isNotEmpty) 'password': password,
            'country': country.toUpperCase(),
            'hub_url': ?hubUrl,
          }),
        )
        .timeout(const Duration(seconds: 10));
    if (response.statusCode != 202) {
      throw http.ClientException('network answered ${response.statusCode}');
    }
  }

  Future<SetupInfo> fetch() async {
    final response = await _http
        .get(baseUri.replace(path: '/api/setup'))
        .timeout(const Duration(seconds: 4));
    if (response.statusCode == 404) throw NotACameraException();
    if (response.statusCode != 200) {
      throw http.ClientException('setup API answered ${response.statusCode}');
    }
    return SetupInfo.fromJson(
      jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>,
    );
  }
}

/// Same hub? Compares scheme, host and port; ignores a trailing slash.
bool sameHub(String a, String b) {
  final x = Uri.parse(a.trim());
  final y = Uri.parse(b.trim());
  return x.scheme == y.scheme && x.host == y.host && x.port == y.port;
}
