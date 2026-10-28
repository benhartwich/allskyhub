// The globe on "Entdecken": the public cameras on the earth, with the night side
// shaded, so one sees at a glance where it is dark now. Drag to turn, pinch to zoom, tap
// a camera to open it.
//
// Orthographic projection from the centre (lat0, lon0). Points on the far side are
// pulled onto the rim, which keeps the land outlines closed polygons; the night side is
// the circle of 90° around the point opposite the sun, drawn the same way. Land outlines:
// Natural Earth 1:110m (public domain), rounded to 0.1° (assets/geo/land.json).

import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../design.dart';

const _rad = math.pi / 180;

/// Longitude/latitude pairs per land outline, loaded once.
Future<List<List<double>>>? _land;

Future<List<List<double>>> loadLand([AssetBundle? bundle]) =>
    _land ??= (bundle ?? rootBundle)
        .loadString('assets/geo/land.json')
        .then(
          (text) => [
            for (final ring in jsonDecode(text) as List<dynamic>)
              [for (final v in ring as List<dynamic>) (v as num).toDouble()],
          ],
        );

/// Where the sun stands overhead at [at] (latitude, longitude in degrees); accurate to
/// a fraction of a degree, plenty for shading the night side.
({double lat, double lon}) subsolarPoint(DateTime at) {
  final n =
      at.toUtc().difference(DateTime.utc(2000, 1, 1, 12)).inSeconds / 86400;
  final l = 280.46 + 0.9856474 * n;
  final g = (357.528 + 0.9856003 * n) * _rad;
  final lambda = (l + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) * _rad;
  final eps = (23.439 - 0.0000004 * n) * _rad;
  final dec = math.asin(math.sin(eps) * math.sin(lambda));
  final ra = math.atan2(math.cos(eps) * math.sin(lambda), math.cos(lambda));
  final gmst = 280.46061837 + 360.98564736629 * n;
  var lon = (ra / _rad - gmst) % 360;
  if (lon > 180) lon -= 360;
  return (lat: dec / _rad, lon: lon);
}

class GlobeMarker {
  const GlobeMarker({
    required this.lat,
    required this.lon,
    required this.night,
  });

  final double lat;
  final double lon;

  /// Shown brighter: the camera is live and it is night there.
  final bool night;
}

class Globe extends StatefulWidget {
  const Globe({
    super.key,
    required this.markers,
    this.onTap,
    this.center = const (lat: 48.0, lon: 10.0),
    this.now,
  });

  final List<GlobeMarker> markers;
  final ValueChanged<int>? onTap;
  final ({double lat, double lon}) center;
  final DateTime? now;

  @override
  State<Globe> createState() => _GlobeState();
}

class _GlobeState extends State<Globe> {
  late double _lat = widget.center.lat;
  late double _lon = widget.center.lon;
  double _zoom = 1;
  double _zoomStart = 1;
  List<List<double>> _rings = const [];

  @override
  void initState() {
    super.initState();
    loadLand().then((rings) {
      if (mounted) setState(() => _rings = rings);
    });
  }

  double _radius(Size size) => size.shortestSide / 2 * 0.92 * _zoom;

  void _tap(Offset at, Size size) {
    final onTap = widget.onTap;
    if (onTap == null) return;
    final p = GlobeProjection(
      _lat,
      _lon,
      size.center(Offset.zero),
      _radius(size),
    );
    var best = -1;
    var bestDistance = 24.0; // a finger's width
    for (final (i, m) in widget.markers.indexed) {
      final q = p.project(m.lat, m.lon);
      if (q == null) continue;
      final d = (q - at).distance;
      if (d < bestDistance) {
        best = i;
        bestDistance = d;
      }
    }
    if (best >= 0) onTap(best);
  }

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, box) {
      final size = box.biggest;
      return GestureDetector(
        onTapUp: (d) => _tap(d.localPosition, size),
        onScaleStart: (_) => _zoomStart = _zoom,
        onScaleUpdate: (d) => setState(() {
          _zoom = (_zoomStart * d.scale).clamp(1.0, 4.0);
          final perDegree = _radius(size) * _rad;
          _lon -= d.focalPointDelta.dx / perDegree;
          _lat = (_lat + d.focalPointDelta.dy / perDegree).clamp(-80.0, 80.0);
          _lon = (_lon + 540) % 360 - 180;
        }),
        child: CustomPaint(
          size: size,
          painter: GlobePainter(
            lat: _lat,
            lon: _lon,
            radius: _radius(size),
            rings: _rings,
            markers: widget.markers,
            sun: subsolarPoint(widget.now ?? DateTime.now()),
          ),
        ),
      );
    },
  );
}

class GlobeProjection {
  GlobeProjection(double lat0, double lon0, this.center, this.radius)
    : _sin0 = math.sin(lat0 * _rad),
      _cos0 = math.cos(lat0 * _rad),
      _lon0 = lon0 * _rad;

  final Offset center;
  final double radius;
  final double _sin0;
  final double _cos0;
  final double _lon0;

  /// On the screen, or null on the far side.
  Offset? project(double lat, double lon) {
    final (x, y, z) = _xyz(lat, lon);
    return z < 0 ? null : center + Offset(x, -y) * radius;
  }

  /// Like [project], but a point on the far side lands on the rim.
  Offset projectClamped(double lat, double lon) {
    final (x, y, z) = _xyz(lat, lon);
    if (z >= 0) return center + Offset(x, -y) * radius;
    final n = math.sqrt(x * x + y * y);
    return n < 1e-9 ? center : center + Offset(x / n, -y / n) * radius;
  }

  (double, double, double) _xyz(double lat, double lon) {
    final phi = lat * _rad, dl = lon * _rad - _lon0;
    final cosPhi = math.cos(phi), sinPhi = math.sin(phi);
    return (
      cosPhi * math.sin(dl),
      _cos0 * sinPhi - _sin0 * cosPhi * math.cos(dl),
      _sin0 * sinPhi + _cos0 * cosPhi * math.cos(dl),
    );
  }
}

class GlobePainter extends CustomPainter {
  GlobePainter({
    required this.lat,
    required this.lon,
    required this.radius,
    required this.rings,
    required this.markers,
    required this.sun,
  });

  final double lat;
  final double lon;
  final double radius;
  final List<List<double>> rings;
  final List<GlobeMarker> markers;
  final ({double lat, double lon}) sun;

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final p = GlobeProjection(lat, lon, c, radius);
    final disc = Rect.fromCircle(center: c, radius: radius);
    canvas.drawCircle(
      c,
      radius * 1.04,
      Paint()
        ..color = Sky.accent.withValues(alpha: .10)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 12),
    );
    canvas.drawCircle(
      c,
      radius,
      Paint()
        ..shader = const RadialGradient(
          center: Alignment(-.3, -.35),
          colors: [Color(0xFF1D3A6E), Color(0xFF071228)],
        ).createShader(disc),
    );
    canvas.save();
    canvas.clipPath(Path()..addOval(disc));
    final land = Paint()..color = const Color(0xFF2C4F86);
    for (final ring in rings) {
      final path = Path();
      for (var i = 0; i + 1 < ring.length; i += 2) {
        final q = p.projectClamped(ring[i + 1], ring[i]);
        i == 0 ? path.moveTo(q.dx, q.dy) : path.lineTo(q.dx, q.dy);
      }
      canvas.drawPath(path..close(), land);
    }
    canvas.drawPath(
      _night(p),
      Paint()..color = const Color(0xFF000000).withValues(alpha: .55),
    );
    canvas.restore();
    canvas.drawCircle(
      c,
      radius,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5
        ..color = Sky.accent.withValues(alpha: .5),
    );
    for (final m in markers) {
      final q = p.project(m.lat, m.lon);
      if (q == null) continue;
      final color = m.night ? Sky.aurora : Sky.warn;
      canvas.drawCircle(q, 9, Paint()..color = color.withValues(alpha: .25));
      canvas.drawCircle(q, 4, Paint()..color = color);
    }
  }

  /// The circle of 90° around the point opposite the sun.
  Path _night(GlobeProjection p) {
    final lat0 = -sun.lat * _rad, lon0 = (sun.lon + 180) * _rad;
    final path = Path();
    for (var i = 0; i <= 120; i++) {
      final b = i / 120 * 2 * math.pi;
      // 90° from (lat0, lon0) in direction b.
      final lat = math.asin(math.cos(lat0) * math.cos(b));
      final lon =
          lon0 +
          math.atan2(
            math.sin(b) * math.cos(lat0),
            -math.sin(lat0) * math.sin(lat),
          );
      final q = p.projectClamped(lat / _rad, lon / _rad);
      i == 0 ? path.moveTo(q.dx, q.dy) : path.lineTo(q.dx, q.dy);
    }
    // The night side holds the antisolar point; seen from behind, fill the rest instead.
    if (p.project(lat0 / _rad, lon0 / _rad) == null) {
      return Path.combine(
        PathOperation.difference,
        Path()..addOval(Rect.fromCircle(center: p.center, radius: p.radius)),
        path..close(),
      );
    }
    return path..close();
  }

  @override
  bool shouldRepaint(GlobePainter old) =>
      old.lat != lat ||
      old.lon != lon ||
      old.radius != radius ||
      old.rings != rings ||
      old.markers != markers ||
      old.sun != sun;
}
