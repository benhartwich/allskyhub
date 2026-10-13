// The phone and the camera's setup network (SPEC §7.1).
//
// Android sends traffic over mobile data when the current Wi-Fi has no internet; binding
// the process to the Wi-Fi keeps requests to 10.42.0.1 on it (MainActivity.kt). iOS
// routes the local subnet over Wi-Fi by itself, so binding is a no-op there.
//
// `joinSetup` joins `allskyhub-XXXX` from the app: Android's WifiNetworkSpecifier (10 and
// later, the phone asks the user to allow it) and iOS's NEHotspotConfiguration.

import 'package:flutter/services.dart';

import '../api/setup_key.dart';

enum JoinResult {
  joined,
  failed,

  /// This phone cannot join from an app (Android before 10): the system settings then.
  unsupported,
}

abstract class WifiBinding {
  /// Returns false if no Wi-Fi network is connected.
  Future<bool> bindToWifi();
  Future<void> unbind();

  /// Joins the camera's setup network; the app's traffic then goes there.
  Future<JoinResult> joinSetup(SetupWifi wifi);
}

class PlatformWifiBinding implements WifiBinding {
  static const _channel = MethodChannel('org.allskyhub.app/wifi');

  @override
  Future<bool> bindToWifi() async {
    try {
      return await _channel.invokeMethod<bool>('bindToWifi') ?? false;
    } on MissingPluginException {
      return true; // iOS
    }
  }

  @override
  Future<void> unbind() async {
    try {
      await _channel.invokeMethod<void>('unbind');
    } on MissingPluginException {
      // iOS
    }
  }

  @override
  Future<JoinResult> joinSetup(SetupWifi wifi) async {
    try {
      final answer = await _channel.invokeMethod<String>('joinSetup', {
        'ssid': wifi.ssid,
        'prefix': setupPrefix,
        'key': wifi.key,
      });
      return switch (answer) {
        'joined' => JoinResult.joined,
        'unsupported' => JoinResult.unsupported,
        _ => JoinResult.failed,
      };
    } on MissingPluginException {
      return JoinResult.unsupported;
    } on PlatformException {
      return JoinResult.failed;
    }
  }
}
