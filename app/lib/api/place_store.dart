// The place "Himmel" shows: the phone's location (asked each time) or a chosen place.
// Stays on the phone.

import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'hub_client.dart';

/// Null place: the phone's own location.
class SkyPlace {
  const SkyPlace.here() : place = null;
  const SkyPlace.at(Place this.place);

  final Place? place;

  bool get here => place == null;
}

abstract class PlaceStore {
  Future<SkyPlace?> load();
  Future<void> save(SkyPlace place);
}

class SecurePlaceStore implements PlaceStore {
  SecurePlaceStore([FlutterSecureStorage? storage])
    : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;
  static const _key = 'sky_place';

  @override
  Future<SkyPlace?> load() async {
    try {
      final raw = await _storage.read(key: _key);
      if (raw == null) return null;
      if (raw == 'here') return const SkyPlace.here();
      return SkyPlace.at(
        Place.fromJson(jsonDecode(raw) as Map<String, dynamic>),
      );
    } on Exception {
      return null;
    }
  }

  @override
  Future<void> save(SkyPlace place) => _storage.write(
    key: _key,
    value: place.here ? 'here' : jsonEncode(place.place!.toJson()),
  );
}

/// For tests.
class MemoryPlaceStore implements PlaceStore {
  MemoryPlaceStore([this.place]);

  SkyPlace? place;

  @override
  Future<SkyPlace?> load() async => place;

  @override
  Future<void> save(SkyPlace place) async => this.place = place;
}
