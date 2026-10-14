// The home-screen widget (roadmap N6, Android only for now): which camera it shows.
//
// The app asks the hub for a read-only widget key (server api/widget.py) and hands it with
// the hub's address to SkyWidget.kt, which keeps them in the app's private preferences and
// fetches the picture itself every 30 minutes. The app's own token never leaves the app.

import 'dart:io' show Platform;

import 'package:flutter/services.dart';

import '../api/hub_client.dart';

enum WidgetResult {
  /// A widget is on the home screen; it shows this camera from now on.
  placed,

  /// The launcher asks the user to place it.
  asked,

  /// The user places it by hand (long press on the home screen, Widgets).
  manual,
}

abstract class SkyWidgetChannel {
  bool get available;
  Future<WidgetResult> show(HubClient client, Camera camera);
}

class PlatformSkyWidget implements SkyWidgetChannel {
  static const _channel = MethodChannel('org.allskyhub.app/widget');

  @override
  bool get available => Platform.isAndroid;

  @override
  Future<WidgetResult> show(HubClient client, Camera camera) async {
    final key = await client.widgetKey(camera.id);
    final answer = await _channel.invokeMethod<String>('configure', {
      'hub': client.baseUrl,
      'key': key,
      'name': camera.name,
      'pin': true,
    });
    return WidgetResult.values.firstWhere(
      (r) => r.name == answer,
      orElse: () => WidgetResult.manual,
    );
  }
}
