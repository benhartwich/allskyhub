// Fireballs sent later (SPEC §10.8): what the hub's /api/v1/devices/<id>/late answers.

/// A wall-clock time as the hub takes it: `2026-11-03T21:45:12`, no zone.
String wallClock(DateTime t) {
  String two(int n) => n.toString().padLeft(2, '0');
  return '${t.year}-${two(t.month)}-${two(t.day)}'
      'T${two(t.hour)}:${two(t.minute)}:${two(t.second)}';
}

/// The time an Allsky file name carries (`image-20261103214512.jpg`), or null.
DateTime? timeFromName(String name) {
  final m = RegExp(
    r'(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})',
  ).firstMatch(name);
  if (m == null) return null;
  final v = [for (var i = 1; i <= 6; i++) int.parse(m.group(i)!)];
  if (v[1] < 1 ||
      v[1] > 12 ||
      v[2] < 1 ||
      v[2] > 31 ||
      v[3] > 23 ||
      v[4] > 59 ||
      v[5] > 59) {
    return null;
  }
  return DateTime(v[0], v[1], v[2], v[3], v[4], v[5]);
}

class LatePair {
  LatePair({
    required this.id,
    required this.other,
    required this.highKm,
    required this.lowKm,
  });

  factory LatePair.fromJson(Map<String, dynamic> json) => LatePair(
    id: json['id'] as String,
    other: json['other'] as String,
    highKm: (json['high_km'] as num).toDouble(),
    lowKm: (json['low_km'] as num).toDouble(),
  );

  final String id;
  final String other;
  final double highKm;
  final double lowKm;
}

class LateFireball {
  LateFireball({
    required this.id,
    required this.state,
    required this.takenAt,
    required this.local,
    required this.exposureS,
    required this.width,
    required this.height,
    this.reason,
    this.reasonText,
    this.track = const [],
    this.stars,
    this.offsetDeg,
    this.peak,
    this.moonConfirmed = false,
    this.orientationUntil,
    this.hidden = false,
    this.pairs = const [],
  });

  factory LateFireball.fromJson(Map<String, dynamic> json) => LateFireball(
    id: json['id'] as String,
    state: json['state'] as String,
    reason: json['reason'] as String?,
    reasonText: json['reason_text'] as String?,
    takenAt: DateTime.parse(json['taken_at'] as String),
    local: DateTime.parse(json['local'] as String),
    exposureS: (json['exposure_s'] as num).toDouble(),
    width: json['width'] as int,
    height: json['height'] as int,
    track: [
      for (final p in json['track'] as List<dynamic>? ?? const [])
        [for (final v in p as List<dynamic>) (v as num).toDouble()],
    ],
    stars: json['stars'] as int?,
    offsetDeg: (json['offset_deg'] as num?)?.toDouble(),
    peak: (json['peak'] as num?)?.toDouble(),
    moonConfirmed: json['moon'] == 'confirmed',
    orientationUntil: json['orientation_until'] == null
        ? null
        : DateTime.parse(json['orientation_until'] as String),
    hidden: json['hidden'] as bool? ?? false,
    pairs: [
      for (final p in json['pairs'] as List<dynamic>? ?? const [])
        LatePair.fromJson(p as Map<String, dynamic>),
    ],
  );

  final String id;

  /// track (waits for its track), checking, accepted or failed.
  final String state;
  final String? reason;
  final String? reasonText;
  final DateTime takenAt;

  /// The mid-exposure in the camera's time zone, as a wall clock.
  final DateTime local;
  final double exposureS;
  final int width;
  final int height;
  final List<List<double>> track;
  final int? stars;
  final double? offsetDeg;
  final double? peak;

  /// The moon stood in the frame where it stood that day: the date is right too.
  final bool moonConfirmed;

  /// Matched the camera's orientation from before it was moved, held until then.
  final DateTime? orientationUntil;
  final bool hidden;
  final List<LatePair> pairs;
}

class LateFireballs {
  LateFireballs({
    required this.sizes,
    required this.zone,
    required this.perDay,
    required this.uploads,
  });

  factory LateFireballs.fromJson(Map<String, dynamic> json) => LateFireballs(
    sizes: [
      for (final s in json['sizes'] as List<dynamic>)
        (width: (s as List<dynamic>)[0] as int, height: s[1] as int),
    ],
    zone: json['zone'] as String,
    perDay: json['per_day'] as int,
    uploads: [
      for (final u in json['uploads'] as List<dynamic>)
        LateFireball.fromJson(u as Map<String, dynamic>),
    ],
  );

  /// The frame sizes an upload may have, the current one first; empty: the camera has
  /// no orientation yet.
  final List<({int width, int height})> sizes;
  final String zone;
  final int perDay;
  final List<LateFireball> uploads;
}
