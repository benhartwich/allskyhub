// Measurements like the web viewer (charts.js): sky brightness, clouds and stars, and for
// the camera exposure, gain, temperatures and disk, over one night or the last hours/days.
// Twilight bands from the sun's altitude, the moon's altitude as a dashed line, a log
// scale for the exposure, gaps where nothing was measured, and one cursor for all charts
// (drag a finger across). Times are the phone's local time.

import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../api/hub_client.dart';

class _Line {
  const _Line(this.key, this.label, this.unit, this.color, {this.digits = 0});

  final String key;
  final String label;
  final String unit;
  final Color color;
  final int digits;
}

class _Panel {
  const _Panel(
    this.title,
    this.source,
    this.lines, {
    this.log = false,
    this.fixed,
    this.right,
    this.moon = false,
  });

  final String title;
  final String source; // "sky" or "status"
  final List<_Line> lines; // first: left axis
  final bool log;
  final (double, double)? fixed;
  final _Line? right; // a second scale on the right
  final bool moon;
}

const _sqm = Color(0xFF7AA2F7);
const _clouds = Color(0xFFA7B0CC);
const _stars = Color(0xFF9ECE6A);
const _exposure = Color(0xFFF2C06B);
const _gain = Color(0xFFB48CFF);
const _sensor = Color(0xFFFF9BD2);
const _cpu = Color(0xFFFF7A93);
const _disk = Color(0xFF8FE3FF);
const _moon = Color(0xFFE6E9F5);

const _panels = [
  _Panel('Himmelshelligkeit (mag/″², ↑ dunkler)', 'sky', [
    _Line('sqm', 'Himmelshelligkeit', 'mag/″²', _sqm, digits: 2),
  ], moon: true),
  _Panel(
    'Bewölkung und Sterne',
    'sky',
    [_Line('cloud', 'Bewölkung', '%', _clouds)],
    fixed: (0, 100),
    right: _Line('stars', 'Sterne', '', _stars),
  ),
  _Panel(
    'Belichtung (s, log) und Gain',
    'status',
    [_Line('exposure', 'Belichtung', 's', _exposure, digits: 3)],
    log: true,
    right: _Line('gain', 'Gain', '', _gain),
  ),
  _Panel(
    'Temperaturen und Speicher',
    'status',
    [
      _Line('sensor', 'Sensor', '°C', _sensor, digits: 1),
      _Line('cpu', 'Prozessor', '°C', _cpu),
    ],
    right: _Line('disk', 'Speicher frei', '%', _disk),
  ),
];

/// The ranges offered, like on the web: one night or the last hours.
const rangeHours = [24, 72, 168, 720];

String _two(int v) => v.toString().padLeft(2, '0');
const _weekdays = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];

DateTime _local(double ts) =>
    DateTime.fromMillisecondsSinceEpoch((ts * 1000).round()).toLocal();

String _clock(double ts) {
  final t = _local(ts);
  return '${_two(t.hour)}:${_two(t.minute)}';
}

String _day(double ts) {
  final t = _local(ts);
  return '${_weekdays[t.weekday - 1]} ${t.day}.${t.month}.';
}

String number(double? v, int digits) {
  if (v == null) return '–';
  final text = digits >= 3
      ? v.toStringAsPrecision(3)
      : v.toStringAsFixed(digits);
  return text.replaceAll('.', ',');
}

String nightTitle(String id) {
  final day = DateTime.utc(
    int.parse(id.substring(0, 4)),
    int.parse(id.substring(4, 6)),
    int.parse(id.substring(6, 8)),
  );
  final next = day.add(const Duration(days: 1));
  return 'Nacht ${day.day}./${next.day}.${next.month}.${next.year}';
}

String shiftNight(String id, int days) {
  final day = DateTime.utc(
    int.parse(id.substring(0, 4)),
    int.parse(id.substring(4, 6)),
    int.parse(id.substring(6, 8)),
  ).add(Duration(days: days));
  return '${day.year}${_two(day.month)}${_two(day.day)}';
}

class MeasurementsScreen extends StatefulWidget {
  const MeasurementsScreen({
    super.key,
    required this.client,
    required this.cameraId,
    this.night,
    this.hours,
  });

  final HubClient client;
  final String cameraId;
  final String? night;
  final int? hours;

  @override
  State<MeasurementsScreen> createState() => _MeasurementsScreenState();
}

class _MeasurementsScreenState extends State<MeasurementsScreen> {
  SeriesData? _data;
  String? _error;
  bool _loading = false;
  final _cursor = ValueNotifier<double?>(null);

  @override
  void initState() {
    super.initState();
    _load(night: widget.night, hours: widget.hours);
  }

  @override
  void dispose() {
    _cursor.dispose();
    super.dispose();
  }

  Future<void> _load({String? night, int? hours}) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await widget.client.series(
        widget.cameraId,
        night: night,
        hours: hours,
      );
      if (mounted) {
        setState(() {
          _data = data;
          _cursor.value = null;
        });
      }
    } on Exception {
      if (mounted) {
        setState(() => _error = 'Die Messdaten konnten nicht geladen werden.');
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  String? get _currentNight {
    final data = _data;
    if (data == null) return null;
    if (data.night != null) return data.night;
    // The night the end of the range belongs to (before noon: the evening before).
    final end = _local(data.to);
    final evening = end.hour < 12 ? end.subtract(const Duration(days: 1)) : end;
    return '${evening.year}${_two(evening.month)}${_two(evening.day)}';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final data = _data;
    final night = _currentNight;
    return Scaffold(
      appBar: AppBar(title: const Text('Messdaten')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
        children: [
          Wrap(
            spacing: 6,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              IconButton(
                key: const Key('night-before'),
                tooltip: 'Nacht davor',
                onPressed: night == null || _loading
                    ? null
                    : () => _load(night: shiftNight(night, -1)),
                icon: const Icon(Icons.chevron_left),
              ),
              ChoiceChip(
                key: const Key('range-night'),
                label: const Text('Nacht'),
                selected: data?.night != null,
                onSelected: (_) => _load(night: night),
              ),
              IconButton(
                key: const Key('night-after'),
                tooltip: 'Nacht danach',
                onPressed: night == null || _loading
                    ? null
                    : () => _load(night: shiftNight(night, 1)),
                icon: const Icon(Icons.chevron_right),
              ),
              for (final h in rangeHours)
                ChoiceChip(
                  key: Key('range-$h'),
                  label: Text(h == 24 ? '24 h' : '${h ~/ 24} Tage'),
                  selected: data?.hours == h,
                  onSelected: (_) => _load(hours: h),
                ),
            ],
          ),
          const SizedBox(height: 6),
          if (data != null)
            Text(
              [
                data.night != null
                    ? nightTitle(data.night!)
                    : '${_day(data.from)} ${_clock(data.from)} – ${_day(data.to)} ${_clock(data.to)}',
                if (data.moonIllumination != null)
                  'Mond ${(data.moonIllumination! * 100).round()} % beleuchtet',
                if (data.bucketSeconds > 0)
                  'Mittelwerte über ${data.bucketSeconds ~/ 60} min',
              ].join(' · '),
              key: const Key('series-meta'),
              style: theme.textTheme.bodySmall,
            ),
          if (_loading) const LinearProgressIndicator(),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.all(12),
              child: Text(
                _error!,
                style: TextStyle(color: theme.colorScheme.error),
              ),
            ),
          if (data != null)
            for (final panel in _panels)
              if (_table(data, panel) case final table?)
                _ChartCard(
                  panel: panel,
                  data: data,
                  table: table,
                  cursor: _cursor,
                ),
          const SizedBox(height: 12),
          Text(
            'Zeiten in der Ortszeit dieses Telefons. Hintergrund von hell nach dunkel: Tag, '
            'Dämmerung, Nacht; gestrichelt die Mondhöhe. Die Himmelshelligkeit ist ein '
            'Näherungswert aus dem Kamerabild, solange die Kamera nicht kalibriert ist.',
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }

  /// The panel's table, or null when it has no value at all in this range.
  SeriesTable? _table(SeriesData data, _Panel panel) {
    final table = panel.source == 'sky' ? data.sky : data.status;
    if (table == null) return null;
    final keys = [...panel.lines.map((l) => l.key), ?panel.right?.key];
    final any = keys.any(
      (k) => (table.columns[k] ?? const []).any((v) => v != null),
    );
    return any ? table : null;
  }
}

class _ChartCard extends StatelessWidget {
  const _ChartCard({
    required this.panel,
    required this.data,
    required this.table,
    required this.cursor,
  });

  final _Panel panel;
  final SeriesData data;
  final SeriesTable table;
  final ValueNotifier<double?> cursor;

  int? _index(double ts) {
    // The nearest sample to the cursor, within a few percent of the range.
    final t = table.t;
    if (t.isEmpty) return null;
    var best = 0;
    for (var i = 1; i < t.length; i++) {
      if ((t[i] - ts).abs() < (t[best] - ts).abs()) best = i;
    }
    return (t[best] - ts).abs() < (data.to - data.from) * 0.03 ? best : null;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final lines = [...panel.lines, ?panel.right];
    return Card(
      key: Key('chart-${panel.lines.first.key}'),
      margin: const EdgeInsets.only(top: 12),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(8, 10, 8, 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 4),
              child: Text(panel.title, style: theme.textTheme.titleSmall),
            ),
            ValueListenableBuilder<double?>(
              valueListenable: cursor,
              builder: (context, ts, _) {
                final i = ts == null ? null : _index(ts);
                return Padding(
                  padding: const EdgeInsets.fromLTRB(4, 4, 4, 2),
                  child: Wrap(
                    spacing: 12,
                    children: [
                      if (ts != null)
                        Text(
                          '${_day(ts)} ${_clock(ts)}',
                          style: theme.textTheme.bodySmall,
                        ),
                      for (final l in lines)
                        Text.rich(
                          TextSpan(
                            children: [
                              TextSpan(
                                text: '● ',
                                style: TextStyle(color: l.color),
                              ),
                              TextSpan(
                                text:
                                    '${l.label}${i == null ? '' : ': ${number(table.columns[l.key]?[i], l.digits)} ${l.unit}'}',
                              ),
                            ],
                          ),
                          style: theme.textTheme.bodySmall,
                        ),
                    ],
                  ),
                );
              },
            ),
            LayoutBuilder(
              builder: (context, box) => GestureDetector(
                onHorizontalDragUpdate: (d) =>
                    cursor.value = _time(d.localPosition.dx, box.maxWidth),
                onHorizontalDragEnd: (_) => cursor.value = null,
                onTapDown: (d) =>
                    cursor.value = _time(d.localPosition.dx, box.maxWidth),
                child: ValueListenableBuilder<double?>(
                  valueListenable: cursor,
                  builder: (context, ts, _) => CustomPaint(
                    size: Size(box.maxWidth, 190),
                    painter: _ChartPainter(
                      panel: panel,
                      data: data,
                      table: table,
                      cursor: ts,
                      text: theme.textTheme.bodySmall?.color ?? Colors.grey,
                      grid: theme.dividerColor.withValues(alpha: 0.35),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  double _time(double x, double width) {
    final f =
        ((x - _ChartPainter.left) /
                (width - _ChartPainter.left - _ChartPainter.right))
            .clamp(0.0, 1.0);
    return data.from + (data.to - data.from) * f;
  }
}

/// A rounded step for about [count] ticks between [min] and [max].
List<double> niceTicks(double min, double max, {int count = 4}) {
  if (!(max > min)) return [min];
  final raw = (max - min) / count;
  final magnitude = math
      .pow(10, (math.log(raw) / math.ln10).floor())
      .toDouble();
  final step = [1, 2, 2.5, 5, 10]
      .map((m) => m * magnitude)
      .firstWhere((s) => s >= raw, orElse: () => 10 * magnitude);
  final ticks = <double>[];
  for (var v = (min / step).ceil() * step; v <= max + step * 1e-9; v += step) {
    ticks.add(double.parse(v.toStringAsFixed(6)));
  }
  return ticks;
}

class _ChartPainter extends CustomPainter {
  _ChartPainter({
    required this.panel,
    required this.data,
    required this.table,
    required this.cursor,
    required this.text,
    required this.grid,
  });

  static const left = 44.0;
  static const right = 40.0;
  static const top = 6.0;
  static const bottom = 20.0;

  final _Panel panel;
  final SeriesData data;
  final SeriesTable table;
  final double? cursor;
  final Color text;
  final Color grid;

  (double, double) _range(
    List<_Line> lines, {
    bool log = false,
    (double, double)? fixed,
  }) {
    if (fixed != null) return fixed;
    final values = [
      for (final l in lines)
        for (final v in table.columns[l.key] ?? const <double?>[])
          if (v != null && (!log || v > 0)) v,
    ];
    if (values.isEmpty) return (0, 1);
    var lo = values.reduce(math.min), hi = values.reduce(math.max);
    if (log) {
      return (
        math.pow(10, (math.log(lo) / math.ln10).floor()).toDouble(),
        math.pow(10, (math.log(hi) / math.ln10).ceil()).toDouble(),
      );
    }
    final pad = math.max((hi - lo) * 0.1, lines.first.digits >= 2 ? 0.25 : 1);
    lo -= pad;
    hi += pad;
    if (lines.first.key == 'stars') lo = 0;
    return (lo, hi);
  }

  void _label(
    Canvas canvas,
    String s,
    Offset at, {
    TextAlign align = TextAlign.left,
  }) {
    final p = TextPainter(
      text: TextSpan(
        text: s,
        style: TextStyle(color: text, fontSize: 10),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final dx = switch (align) {
      TextAlign.right => at.dx - p.width,
      TextAlign.center => at.dx - p.width / 2,
      _ => at.dx,
    };
    p.paint(canvas, Offset(dx, at.dy - p.height / 2));
  }

  @override
  void paint(Canvas canvas, Size size) {
    final plot = Rect.fromLTRB(
      left,
      top,
      size.width - right,
      size.height - bottom,
    );
    double x(double ts) =>
        plot.left + plot.width * (ts - data.from) / (data.to - data.from);

    // Twilight: day, civil, nautical, astronomical, night (from the sun's altitude).
    final eph = data.ephemeris;
    final sun = eph?.columns['sun'];
    if (eph != null && sun != null) {
      int band(double? alt) => alt == null
          ? -1
          : alt > 0
          ? 0
          : alt > -6
          ? 1
          : alt > -12
          ? 2
          : alt > -18
          ? 3
          : -1;
      const alphas = [0.16, 0.11, 0.07, 0.04];
      for (var i = 0; i + 1 < eph.t.length; i++) {
        final b = band(sun[i]);
        if (b < 0) continue;
        canvas.drawRect(
          Rect.fromLTRB(x(eph.t[i]), plot.top, x(eph.t[i + 1]), plot.bottom),
          Paint()..color = const Color(0xFF4A6CD4).withValues(alpha: alphas[b]),
        );
      }
    }

    // Time axis: steps of hours or days, so about five labels fit.
    final span = data.to - data.from;
    const steps = <double>[
      3600,
      7200,
      10800,
      21600,
      43200,
      86400,
      172800,
      604800,
    ];
    final step = steps.firstWhere((s) => span / s <= 6, orElse: () => 604800);
    final gridPaint = Paint()
      ..color = grid
      ..strokeWidth = 1;
    final offset = _local(data.from).timeZoneOffset.inSeconds;
    for (
      var ts = ((data.from + offset) / step).ceil() * step - offset;
      ts <= data.to;
      ts += step
    ) {
      final px = x(ts);
      canvas.drawLine(Offset(px, plot.top), Offset(px, plot.bottom), gridPaint);
      final local = _local(ts);
      final label = step >= 86400 || (local.hour == 0 && local.minute == 0)
          ? _day(ts)
          : _clock(ts);
      _label(
        canvas,
        label,
        Offset(px, plot.bottom + 10),
        align: TextAlign.center,
      );
    }

    // Left scale.
    final (lo, hi) = _range(panel.lines, log: panel.log, fixed: panel.fixed);
    double yLin(double v, double a, double b) =>
        plot.bottom - plot.height * ((v - a) / (b - a)).clamp(-0.05, 1.05);
    double yLeft(double v) => panel.log
        ? yLin(
            math.log(v) / math.ln10,
            math.log(lo) / math.ln10,
            math.log(hi) / math.ln10,
          )
        : yLin(v, lo, hi);
    final leftTicks = panel.log
        ? [
            for (
              var e = (math.log(lo) / math.ln10).round();
              e <= (math.log(hi) / math.ln10).round();
              e++
            )
              math.pow(10, e).toDouble(),
          ]
        : niceTicks(lo, hi);
    for (final v in leftTicks) {
      final py = yLeft(v);
      canvas.drawLine(Offset(plot.left, py), Offset(plot.right, py), gridPaint);
      _label(
        canvas,
        number(v, v.abs() < 10 && v % 1 != 0 ? (panel.log ? 3 : 2) : 0),
        Offset(plot.left - 4, py),
        align: TextAlign.right,
      );
    }

    // Right scale: a second quantity, or the moon's altitude.
    double Function(double)? yRight;
    if (panel.right != null) {
      final (rlo, rhi) = _range([
        panel.right!,
      ], fixed: panel.right!.key == 'disk' ? (0, 100) : null);
      yRight = (v) => yLin(v, rlo, rhi);
      for (final v in niceTicks(rlo, rhi)) {
        _label(canvas, number(v, 0), Offset(plot.right + 4, yRight(v)));
      }
    }

    canvas.save();
    canvas.clipRect(plot);
    if (panel.moon && eph != null && eph.columns['moon'] != null) {
      double yMoon(double alt) => yLin(alt, -30, 90);
      _path(
        canvas,
        eph.t,
        eph.columns['moon']!,
        x,
        yMoon,
        _moon.withValues(alpha: 0.7),
        dashed: true,
      );
    }
    for (final l in panel.lines) {
      _path(
        canvas,
        table.t,
        table.columns[l.key] ?? const [],
        x,
        yLeft,
        l.color,
        skipZero: panel.log,
      );
    }
    if (panel.right != null && yRight != null) {
      _path(
        canvas,
        table.t,
        table.columns[panel.right!.key] ?? const [],
        x,
        yRight,
        panel.right!.color,
      );
    }
    canvas.restore();

    if (cursor != null) {
      final px = x(cursor!);
      canvas.drawLine(
        Offset(px, plot.top),
        Offset(px, plot.bottom),
        Paint()
          ..color = text.withValues(alpha: 0.7)
          ..strokeWidth = 1,
      );
    }
  }

  void _path(
    Canvas canvas,
    List<double> t,
    List<double?> values,
    double Function(double) x,
    double Function(double) y,
    Color color, {
    bool dashed = false,
    bool skipZero = false,
  }) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.6
      ..style = PaintingStyle.stroke
      ..strokeJoin = StrokeJoin.round;
    final path = Path();
    var open = false;
    var count = 0;
    for (var i = 0; i < t.length && i < values.length; i++) {
      final v = values[i];
      if (v == null || (skipZero && v <= 0)) {
        open = false; // a gap: no line across it
        continue;
      }
      final p = Offset(x(t[i]), y(v));
      if (open) {
        path.lineTo(p.dx, p.dy);
      } else {
        path.moveTo(p.dx, p.dy);
        open = true;
      }
      count++;
    }
    if (count == 0) return;
    if (!dashed) {
      canvas.drawPath(path, paint);
      return;
    }
    for (final metric in path.computeMetrics()) {
      for (var d = 0.0; d < metric.length; d += 9) {
        canvas.drawPath(
          metric.extractPath(d, math.min(d + 5, metric.length)),
          paint,
        );
      }
    }
  }

  @override
  bool shouldRepaint(_ChartPainter old) =>
      old.cursor != cursor || old.data != data || old.text != text;
}
