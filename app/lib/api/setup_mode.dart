// Setting up a new camera through its own Wi-Fi (SPEC §6.2 step 1, §7.1, §7.2):
// connect to `allskyhub-XXXX`, choose the home Wi-Fi, send it with the app's hub, then find
// the camera on the home network. Pairing then continues with OnboardingController.

import 'package:flutter/foundation.dart';

import '../platform/wifi_binding.dart';
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
    CameraSetupClient? setup,
    this.searchTimeout = const Duration(minutes: 2),
  }) : setup = setup ?? CameraSetupClient(setupModeUri);

  final HubClient hub;
  final WifiBinding binding;
  final CameraDiscovery discovery;
  final CameraSetupClient setup;
  final Duration searchTimeout;

  SetupPhase phase = SetupPhase.joinCameraNetwork;
  SetupInfo? info;
  List<WifiNetwork> networks = const [];
  Uri? cameraUri;
  String? error;

  void _set(SetupPhase next, {String? error}) {
    phase = next;
    this.error = error;
    notifyListeners();
  }

  /// After the user joined `allskyhub-XXXX`.
  Future<void> connect() async {
    _set(SetupPhase.connecting);
    await binding.bindToWifi();
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
    _set(SetupPhase.sending);
    try {
      await setup.sendNetwork(
        ssid: ssid,
        password: password,
        country: country,
        hubUrl: hub.baseUrl,
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
