// Pairing without typing (SPEC §6.2 steps 2–4): read the code from the camera's setup API,
// claim it at the hub, wait until the camera reports `paired`.

import 'dart:async';

import 'package:flutter/foundation.dart';

import 'camera_setup.dart';
import 'hub_client.dart';

enum OnboardingPhase {
  /// Looking for the camera's setup API.
  connecting,

  /// The camera answers but has no code yet (it is still registering at the hub).
  waitingForCode,

  /// Claiming the code at the hub.
  claiming,

  /// Claimed; waiting for the camera to see it on its next registration.
  waitingForCamera,
  done,
  failed,
}

class OnboardingController extends ChangeNotifier {
  OnboardingController({
    required this.setup,
    required this.hub,
    this.name = '',
    this.interval = const Duration(seconds: 2),
    this.pairedTimeout = const Duration(seconds: 60),
  });

  final CameraSetupClient setup;
  final HubClient hub;
  final String name;
  final Duration interval;

  /// After a successful claim the camera needs one more registration (about 5 s) to see
  /// it; past this, onboarding finishes anyway: the camera is bound at the hub.
  final Duration pairedTimeout;

  OnboardingPhase phase = OnboardingPhase.connecting;
  SetupInfo? info;
  Camera? camera;
  String? error;

  Timer? _timer;
  bool _busy = false;
  String? _claimedCode;
  DateTime? _claimedAt;

  void start() {
    _tick();
    _timer = Timer.periodic(interval, (_) => _tick());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  void _set(OnboardingPhase next, {String? error}) {
    phase = next;
    this.error = error;
    if (next == OnboardingPhase.done || next == OnboardingPhase.failed) {
      _timer?.cancel();
    }
    notifyListeners();
  }

  /// One polling step; public for tests.
  @visibleForTesting
  Future<void> tick() => _tick();

  Future<void> _tick() async {
    if (_busy ||
        phase == OnboardingPhase.done ||
        phase == OnboardingPhase.failed) {
      return;
    }
    _busy = true;
    try {
      final SetupInfo current;
      try {
        current = await setup.fetch();
      } on NotACameraException {
        _set(
          OnboardingPhase.failed,
          error:
              'Unter dieser Adresse antwortet keine eingerichtete allskyhub-Kamera.',
        );
        return;
      } on Exception {
        if (_claimedAt == null) {
          _set(OnboardingPhase.connecting);
        } else {
          _checkTimeout();
        }
        return;
      }
      info = current;
      if (!sameHub(current.hubUrl, hub.baseUrl)) {
        _set(
          OnboardingPhase.failed,
          error:
              'Die Kamera ist mit einem anderen Hub verbunden (${current.hubUrl}).',
        );
        return;
      }
      if (current.paired) {
        try {
          camera ??= await _cameraOrNull(current.deviceId);
        } on Exception {
          return; // hub briefly unreachable: try again on the next tick
        }
        if (camera == null) {
          _set(
            OnboardingPhase.failed,
            error: 'Die Kamera ist bereits mit einem anderen Konto gekoppelt.',
          );
        } else {
          _set(OnboardingPhase.done);
        }
        return;
      }
      final code = current.pairingCode;
      if (_claimedAt != null) {
        _checkTimeout();
        return;
      }
      if (code == null) {
        _set(OnboardingPhase.waitingForCode);
        return;
      }
      if (code == _claimedCode) return;
      _claimedCode = code;
      _set(OnboardingPhase.claiming);
      try {
        camera = await hub.claim(code, name: name);
        _claimedAt = DateTime.now();
        _set(OnboardingPhase.waitingForCamera);
      } on HubException catch (e) {
        // An expired code: the camera shows a new one on its next registration.
        if (e.statusCode == 400) {
          _set(OnboardingPhase.waitingForCode);
        } else {
          _set(OnboardingPhase.failed, error: e.message);
        }
      }
    } finally {
      _busy = false;
    }
  }

  void _checkTimeout() {
    if (_claimedAt != null &&
        DateTime.now().difference(_claimedAt!) >= pairedTimeout) {
      _set(OnboardingPhase.done);
    }
  }

  Future<Camera?> _cameraOrNull(String id) async {
    try {
      return await hub.camera(id);
    } on HubException catch (e) {
      if (e.statusCode == 404) return null;
      rethrow;
    }
  }
}
