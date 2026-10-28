// Push notifications through Firebase Cloud Messaging (stage 5). The app needs no Firebase
// files: the hub hands out its public Firebase identifiers (GET /api/v1/push/config), the
// app registers with them at runtime and tells the hub its token (POST /push/tokens).
// Nothing happens until the hub has push and the person switches a notification on.

import 'dart:io' show Platform;

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';

import '../api/hub_client.dart';

abstract class PushService {
  /// Registers this phone with the hub; asks for permission when [ask] (a switch was
  /// turned on), else only if it was given before. False when push is not possible (no
  /// config on the hub, no permission).
  Future<bool> register(HubClient client, {bool ask = true});

  /// On sign-out: the phone stops receiving this account's notifications.
  Future<void> unregister(HubClient client);
}

class FirebasePush implements PushService {
  String? _token;

  Future<FirebaseMessaging?> _messaging(HubClient client) async {
    final config = await client.pushConfig();
    if (config == null) return null;
    final android = Platform.isAndroid;
    final appId = android ? config.androidAppId : config.iosAppId;
    final apiKey = android ? config.androidApiKey : config.iosApiKey;
    if (appId.isEmpty || apiKey.isEmpty) return null;
    if (Firebase.apps.isEmpty) {
      await Firebase.initializeApp(
        options: FirebaseOptions(
          apiKey: apiKey,
          appId: appId,
          messagingSenderId: config.senderId,
          projectId: config.projectId,
          iosBundleId: android ? null : 'org.allskyhub.app',
        ),
      );
    }
    return FirebaseMessaging.instance;
  }

  @override
  Future<bool> register(HubClient client, {bool ask = true}) async {
    try {
      final messaging = await _messaging(client);
      if (messaging == null) return false;
      var settings = await messaging.getNotificationSettings();
      if (settings.authorizationStatus == AuthorizationStatus.notDetermined) {
        if (!ask) return false;
        settings = await messaging.requestPermission();
      }
      if (settings.authorizationStatus == AuthorizationStatus.denied) {
        return false;
      }
      // The registration token the hub's FCM HTTP v1 sender addresses (push/fcm.py);
      // firebase_messaging 17's register()/onRegistered hand out installation ids for
      // the newer direct send instead, which the hub does not speak.
      // ignore: deprecated_member_use
      final token = await messaging.getToken();
      if (token == null) return false;
      _token = token;
      await client.registerPushToken(
        token,
        Platform.isAndroid ? 'android' : 'ios',
      );
      // ignore: deprecated_member_use
      messaging.onTokenRefresh.listen(
        (fresh) => client
            .registerPushToken(fresh, Platform.isAndroid ? 'android' : 'ios')
            .catchError((Object _) {}),
      );
      return true;
    } on Exception {
      return false;
    }
  }

  @override
  Future<void> unregister(HubClient client) async {
    final token = _token;
    if (token == null) return;
    try {
      await client.unregisterPushToken(
        token,
        Platform.isAndroid ? 'android' : 'ios',
      );
    } on Exception {
      // Offline: the hub drops the token when FCM reports it dead.
    }
  }
}

/// For tests and platforms without push.
class NoPush implements PushService {
  int registered = 0;

  @override
  Future<bool> register(HubClient client, {bool ask = true}) async {
    registered++;
    return false;
  }

  @override
  Future<void> unregister(HubClient client) async {}
}

/// The app's push service; tests put [NoPush] here.
PushService pushService = FirebasePush();
