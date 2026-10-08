// A night's sky measurements as small line charts (SPEC §6.3 status.sky).

import 'package:flutter/material.dart';

import '../api/hub_client.dart';

class SkySeries {
  const SkySeries(
    this.title,
    this.unit,
    this.value,
    this.low,
    this.high,
    this.color, {
    this.decimals = 0,
  });

  final String title;
  final String unit;
  final double? Function(SkySample) value;

  /// Fixed scale; high <= low means 0..maximum of the night (stars).
  final double low;
  final double high;
  final Color color;
  final int decimals;
}

final skySeries = [
  SkySeries(
    'Bewölkung',
    '%',
    (s) => s.cloudCover == null ? null : s.cloudCover! * 100,
    0,
    100,
    const Color(0xFFA3ABC4),
  ),
  SkySeries(
    'Himmelshelligkeit',
    'mag/″²',
    (s) => s.sqmMag,
    16,
    22,
    const Color(0xFF7AA2F7),
    decimals: 2,
  ),
  SkySeries(
    'Sterne',
    '',
    (s) => s.stars?.toDouble(),
    0,
    0,
    const Color(0xFF73DACA),
  ),
];

/// Runs of consecutive values; a missing value (null) ends a run: gaps, never zeros.
List<List<(DateTime, double)>> skySegments(
  List<SkySample> samples,
  SkySeries series,
) {
  final segments = <List<(DateTime, double)>>[[]];
  for (final sample in samples) {
    final value = series.value(sample);
    if (value == null) {
      if (segments.last.isNotEmpty) segments.add([]);
    } else {
      segments.last.add((sample.at, value));
    }
  }
  return segments.where((s) => s.isNotEmpty).toList();
}

class SkyCharts extends StatelessWidget {
  const SkyCharts({super.key, required this.samples});

  final List<SkySample> samples;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final charts = <Widget>[];
    for (final series in skySeries) {
      final segments = skySegments(samples, series);
      if (segments.isEmpty) continue;
      final values = [
        for (final seg in segments)
          for (final (_, v) in seg) v,
      ];
      var low = series.low;
      var high = series.high;
      if (high <= low) {
        low = 0;
        high = values.reduce((a, b) => a > b ? a : b).clamp(1, double.infinity);
      }
      low = [low, ...values].reduce((a, b) => a < b ? a : b);
      high = [high, ...values].reduce((a, b) => a > b ? a : b);
      charts.add(
        Padding(
          key: ValueKey('sky-${series.title}'),
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '${series.title} · zuletzt ${values.last.toStringAsFixed(series.decimals)}'
                '${series.unit.isEmpty ? '' : ' ${series.unit}'}',
                style: theme.textTheme.bodySmall,
              ),
              const SizedBox(height: 4),
              SizedBox(
                height: 70,
                width: double.infinity,
                child: CustomPaint(
                  painter: _LinePainter(
                    segments: segments,
                    start: samples.first.at,
                    end: samples.last.at,
                    low: low,
                    high: high,
                    color: series.color,
                    background: theme.colorScheme.surfaceContainerHighest,
                  ),
                ),
              ),
            ],
          ),
        ),
      );
    }
    if (charts.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Himmel in dieser Nacht', style: theme.textTheme.titleSmall),
          ...charts,
        ],
      ),
    );
  }
}

class _LinePainter extends CustomPainter {
  _LinePainter({
    required this.segments,
    required this.start,
    required this.end,
    required this.low,
    required this.high,
    required this.color,
    required this.background,
  });

  final List<List<(DateTime, double)>> segments;
  final DateTime start;
  final DateTime end;
  final double low;
  final double high;
  final Color color;
  final Color background;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRRect(
      RRect.fromRectAndRadius(Offset.zero & size, const Radius.circular(8)),
      Paint()..color = background,
    );
    final span = end
        .difference(start)
        .inMilliseconds
        .clamp(1, 1 << 62)
        .toDouble();
    Offset point(DateTime at, double v) => Offset(
      4 + (size.width - 8) * at.difference(start).inMilliseconds / span,
      size.height - 4 - (size.height - 8) * (v - low) / (high - low),
    );
    final line = Paint()
      ..color = color
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;
    for (final seg in segments) {
      if (seg.length == 1) {
        canvas.drawCircle(
          point(seg.first.$1, seg.first.$2),
          2.5,
          Paint()..color = color,
        );
        continue;
      }
      final path = Path()
        ..moveTo(
          point(seg.first.$1, seg.first.$2).dx,
          point(seg.first.$1, seg.first.$2).dy,
        );
      for (final (at, v) in seg.skip(1)) {
        final p = point(at, v);
        path.lineTo(p.dx, p.dy);
      }
      canvas.drawPath(path, line);
    }
  }

  @override
  bool shouldRepaint(_LinePainter old) => old.segments != segments;
}
