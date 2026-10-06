// Where the app keeps the hub URL and its token.

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class StoredSession {
  const StoredSession(this.hubUrl, this.token);

  final String hubUrl;
  final String token;
}

abstract class SessionStore {
  Future<StoredSession?> load();
  Future<void> save(StoredSession session);
  Future<void> clear();
}

/// Keychain (iOS) / Keystore-backed storage (Android).
class SecureSessionStore implements SessionStore {
  SecureSessionStore([FlutterSecureStorage? storage])
    : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;
  static const _hubKey = 'hub_url';
  static const _tokenKey = 'app_token';

  @override
  Future<StoredSession?> load() async {
    final hub = await _storage.read(key: _hubKey);
    final token = await _storage.read(key: _tokenKey);
    return hub == null || token == null ? null : StoredSession(hub, token);
  }

  @override
  Future<void> save(StoredSession session) async {
    await _storage.write(key: _hubKey, value: session.hubUrl);
    await _storage.write(key: _tokenKey, value: session.token);
  }

  @override
  Future<void> clear() async {
    await _storage.delete(key: _tokenKey);
  }
}

/// For tests.
class MemorySessionStore implements SessionStore {
  StoredSession? session;

  @override
  Future<StoredSession?> load() async => session;

  @override
  Future<void> save(StoredSession session) async => this.session = session;

  @override
  Future<void> clear() async => session = null;
}
