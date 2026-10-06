// Keeps the app's traffic on a Wi-Fi without internet (the camera's setup network).
//
// Android sends traffic over mobile data when the current Wi-Fi has no internet; binding
// the process to the Wi-Fi keeps requests to 10.42.0.1 on it (MainActivity.kt). iOS
// routes the local subnet over Wi-Fi by itself, so this is a no-op there.

import 'package:flutter/services.dart';

abstract class WifiBinding {
  /// Returns false if no Wi-Fi network is connected.
  Future<bool> bindToWifi();
  Future<void> unbind();
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
}
