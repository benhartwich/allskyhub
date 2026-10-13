// Setting up a new camera through its own Wi-Fi (SPEC §6.2 step 1, §7.1, §7.2):
// connect to `allskyhub-XXXX`, choose the home Wi-Fi, send it with the app's hub, then find
// the camera on the home network. Pairing then continues with OnboardingController.

import 'package:flutter/foundation.dart';

import '../platform/location.dart';
import '../platform/wifi_binding.dart';
import 'setup_key.dart';
import 'camera_setup.dart';
import 'discovery.dart';
import 'hub_client.dart';

enum SetupPhase {
  /// The user joins the camera's network in the system settings.
  joinCameraNetwork,
  connecting,
  chooseNetwork,
  sending,

  /// The camera joins the home Wi-Fi; the phone goes back to it; discovery runs.
  searching,

  /// Found on the home network: `cameraUri` is set.
  found,

  /// Not found by discovery: the address can be typed (or the camera reopened its setup
  /// network after a failure, see `SetupInfo.lastError`).
  notFound,
}

class SetupModeController extends ChangeNotifier {
  SetupModeController({
    required this.hub,
    required this.binding,
    required this.discovery,
    required this.locationSource,
    CameraSetupClient? setup,
    this.searchTimeout = const Duration(minutes: 2),
  }) : setup = setup ?? CameraSetupClient(setupModeUri);

  final HubClient hub;
  final WifiBinding binding;
  final CameraDiscovery discovery;
  final LocationSource locationSource;
  final CameraSetupClient setup;
  final Duration searchTimeout;

  SetupPhase phase = SetupPhase.joinCameraNetwork;
  SetupInfo? info;
  List<WifiNetwork> networks = const [];
  Uri? cameraUri;
  String? error;

  /// The camera's setup network was open: an agent from before the setup key (SPEC §7.1).
  bool outdatedCamera = false;

  /// Where the camera stands (SPEC §7.1): from the phone or typed in.
  CameraLocation? location;
  LocationProblem? locationProblem;
  bool locating = false;

  /// The phone's IANA time zone, sent along with the location.
  String? timezone;

  /// Use the phone's position (asks for permission).
  Future<void> locate() async {
    locating = true;
    notifyListeners();
    final result = await locationSource.current();
    locating = false;
    location = result.location ?? location;
    locationProblem = result.problem;
    notifyListeners();
  }

  void setLocation(CameraLocation? value) {
    location = value;
    notifyListeners();
  }

  void _set(SetupPhase next, {String? error}) {
    phase = next;
    this.error = error;
    notifyListeners();
  }

  /// Joins the camera's setup network from the app (QR code, typed key, or open for an
  /// older camera), then goes on as [connect].
  Future<void> join(SetupWifi wifi) async {
    _set(SetupPhase.connecting);
    final result = await binding.joinSetup(wifi);
    switch (result) {
      case JoinResult.joined:
        outdatedCamera = wifi.open;
        await connect(alreadyBound: true);
      case JoinResult.unsupported:
        _set(
          SetupPhase.joinCameraNetwork,
          error:
              'Dieses Handy kann sich nicht aus der App verbinden. Verbinde dich in den '
              'WLAN-Einstellungen mit „${wifi.ssid ?? 'allskyhub-…'}“'
              '${wifi.key == null ? '' : ' (Schlüssel ${_shown(wifi.key!)})'} und tippe dann '
              'auf „Ich bin verbunden“.',
        );
      case JoinResult.failed:
        _set(
          SetupPhase.joinCameraNetwork,
          error: wifi.open
              ? 'Kein offenes Netz „allskyhub-…“ gefunden. Ist die Kamera eingeschaltet?'
              : 'Verbindung fehlgeschlagen. Stimmt der Schlüssel, und ist die Kamera in '
                    'der Nähe und eingeschaltet?',
        );
    }
  }

  static String _shown(String key) => key.length == 12
      ? '${key.substring(0, 4)}-${key.substring(4, 8)}-${key.substring(8)}'
      : key;

  /// After the user (or [join]) joined `allskyhub-XXXX`.
  Future<void> connect({bool alreadyBound = false}) async {
    _set(SetupPhase.connecting);
    if (!alreadyBound) await binding.bindToWifi();
    try {
      final current = await setup.fetch();
      if (!current.setupMode) {
        await binding.unbind();
        _set(
          SetupPhase.joinCameraNetwork,
          error:
              'Die Kamera ist nicht im Einrichtungsmodus. Sie ist schon in einem WLAN – '
              'nutze „Kamera im WLAN einrichten“.',
        );
        return;
      }
      info = current;
      networks = await setup.networks();
      timezone ??= await locationSource.timezone();
      _set(
        SetupPhase.chooseNetwork,
        error: describeSetupError(current.lastError),
      );
    } on Exception {
      await binding.unbind();
      _set(
        SetupPhase.joinCameraNetwork,
        error:
            'Kamera nicht erreichbar. Ist dein Handy mit dem WLAN „allskyhub-…“ verbunden?',
      );
    }
  }

  Future<void> refreshNetworks() async {
    try {
      networks = await setup.networks();
      notifyListeners();
    } on Exception {
      _set(
        SetupPhase.chooseNetwork,
        error: 'Die WLAN-Liste konnte nicht geladen werden.',
      );
    }
  }

  /// Sends the home Wi-Fi and the app's hub, then looks for the camera at home.
  Future<void> send({
    required String ssid,
    String? password,
    required String country,
  }) async {
    // SPEC §7.1: the camera's hub token must never travel over plain ws.
    if (!hub.baseUrl.startsWith('https://')) {
      _set(
        SetupPhase.chooseNetwork,
        error: 'Die Hub-Adresse muss mit https:// beginnen (${hub.baseUrl}).',
      );
      return;
    }
    _set(SetupPhase.sending);
    try {
      await setup.sendNetwork(
        ssid: ssid,
        password: password,
        country: country,
        hubUrl: hub.baseUrl,
        latitude: location?.latitude,
        longitude: location?.longitude,
        timezone: timezone,
      );
    } on Exception {
      _set(
        SetupPhase.chooseNetwork,
        error:
            'Die Kamera hat die WLAN-Daten nicht angenommen. Bitte erneut versuchen.',
      );
      return;
    }
    await binding.unbind();
    await search();
  }

  Future<void> search() async {
    final deviceId = info?.deviceId;
    if (deviceId == null) return;
    _set(SetupPhase.searching);
    Uri? uri;
    try {
      uri = await discovery.find(deviceId, timeout: searchTimeout);
    } on Exception {
      uri = null;
    }
    cameraUri = uri;
    _set(uri == null ? SetupPhase.notFound : SetupPhase.found);
  }

  /// Back to the start, e.g. when the camera reopened its setup network after a failure.
  void restart() {
    info = null;
    networks = const [];
    cameraUri = null;
    _set(SetupPhase.joinCameraNetwork);
  }
}
