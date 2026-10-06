// The camera's local setup API (SPEC §7), reached over the LAN or its setup hotspot.

import 'dart:convert';

import 'package:http/http.dart' as http;

/// Default port of the agent's local web server.
const cameraSetupPort = 8080;

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
  });

  factory SetupInfo.fromJson(Map<String, dynamic> json) => SetupInfo(
    deviceId: json['device_id'] as String,
    hubUrl: json['hub_url'] as String,
    paired: json['paired'] as bool,
    pairingCode: json['pairing_code'] as String?,
    expiresIn: json['expires_in'] as int?,
    profile: (json['profile'] as String?) ?? '',
    agentVersion: (json['agent_version'] as String?) ?? '',
  );

  final String deviceId;
  final String hubUrl;
  final bool paired;

  /// Raw 6-character code (SPEC §6.2), null once paired or before the first registration.
  final String? pairingCode;
  final int? expiresIn;
  final String profile;
  final String agentVersion;
}

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

  Future<SetupInfo> fetch() async {
    final response = await _http
        .get(baseUri.replace(path: '/api/setup'))
        .timeout(const Duration(seconds: 4));
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
