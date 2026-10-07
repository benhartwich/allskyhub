// The phone's coarse location and time zone for the camera's setup (SPEC §7.1): the camera
// needs them for day/night (§4.2) and its night folders (§4.5).

import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:geolocator/geolocator.dart';

/// Where the camera stands. Rounded to two decimals (about 1 km): enough for the sun.
class CameraLocation {
  CameraLocation(double latitude, double longitude)
    : latitude = _round(latitude),
      longitude = _round(longitude);

  final double latitude;
  final double longitude;

  static double _round(double v) => (v * 100).roundToDouble() / 100;

  /// Parses manual input ("48,14" or "48.14"); null when not a valid coordinate pair.
  static CameraLocation? tryParse(String latitude, String longitude) {
    double? parse(String s) => double.tryParse(s.trim().replaceAll(',', '.'));
    final lat = parse(latitude);
    final lon = parse(longitude);
    if (lat == null || lon == null) return null;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
    return CameraLocation(lat, lon);
  }
}

/// Why the phone's location is not available.
enum LocationProblem { serviceOff, denied, deniedForever, failed }

class LocationResult {
  const LocationResult.found(CameraLocation this.location) : problem = null;
  const LocationResult.missing(LocationProblem this.problem) : location = null;

  final CameraLocation? location;
  final LocationProblem? problem;
}

abstract class LocationSource {
  /// Asks for permission if needed.
  Future<LocationResult> current();

  /// IANA name like "Europe/Vienna", or null.
  Future<String?> timezone();
}

class PlatformLocationSource implements LocationSource {
  @override
  Future<LocationResult> current() async {
    try {
      if (!await Geolocator.isLocationServiceEnabled()) {
        return const LocationResult.missing(LocationProblem.serviceOff);
      }
      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.deniedForever) {
        return const LocationResult.missing(LocationProblem.deniedForever);
      }
      if (permission == LocationPermission.denied) {
        return const LocationResult.missing(LocationProblem.denied);
      }
      Position? position;
      try {
        position = await Geolocator.getCurrentPosition(
          locationSettings: const LocationSettings(
            accuracy: LocationAccuracy.low,
            timeLimit: Duration(seconds: 15),
          ),
        );
      } on Exception {
        position = await Geolocator.getLastKnownPosition();
      }
      if (position == null) {
        return const LocationResult.missing(LocationProblem.failed);
      }
      return LocationResult.found(
        CameraLocation(position.latitude, position.longitude),
      );
    } on Exception {
      return const LocationResult.missing(LocationProblem.failed);
    }
  }

  @override
  Future<String?> timezone() async {
    try {
      return (await FlutterTimezone.getLocalTimezone()).identifier;
    } on Exception {
      return null;
    }
  }
}

String describeLocationProblem(LocationProblem problem) => switch (problem) {
  LocationProblem.serviceOff =>
    'Die Standortdienste des Handys sind aus. Schalte sie ein oder gib die Koordinaten ein.',
  LocationProblem.denied =>
    'Ohne Freigabe kennt die App den Standort nicht. Gib die Koordinaten der Kamera ein.',
  LocationProblem.deniedForever =>
    'Der Standortzugriff ist in den Einstellungen gesperrt. Gib die Koordinaten der Kamera ein.',
  LocationProblem.failed =>
    'Der Standort konnte nicht bestimmt werden. Gib die Koordinaten der Kamera ein.',
};
