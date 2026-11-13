// The camera's sky in the app (drafts 1, 2, 4 and 5, 2026-11-13): layers over the live
// picture, how well the camera knows its orientation, the "Himmel jetzt" tiles and the
// bright satellites. The tiles and the orientation come from the camera's status (SPEC
// §6.3); the layers and satellites from the hub (GET /cameras/{id}/sky-overlay and
// /satellites).

import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import '../design.dart';
import '../l10n.dart';
import 'camera_status.dart' show formatAge;

// ---- Layers over the live picture (draft 1) -------------------------------------------

enum SkyLayer { constellations, stars, grid, planets, aircraft, satellites }

String layerTitle(SkyLayer l) => switch (l) {
  SkyLayer.constellations => tr('Sternbilder', 'Constellations'),
  SkyLayer.stars => tr('Sternnamen', 'Star names'),
  SkyLayer.grid => tr('Raster', 'Grid'),
  SkyLayer.planets => tr('Mond & Planeten', 'Moon & planets'),
  SkyLayer.aircraft => tr('Flugzeuge', 'Aircraft'),
  SkyLayer.satellites => tr('Satelliten', 'Satellites'),
};

/// The layers switched on, kept while the app runs (like the hub's page per browser).
final shownLayers = ValueNotifier<Set<SkyLayer>>(<SkyLayer>{});

class LayerChips extends StatelessWidget {
  const LayerChips({super.key});

  @override
  Widget build(BuildContext context) => ValueListenableBuilder<Set<SkyLayer>>(
    valueListenable: shownLayers,
    builder: (context, shown, _) => Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [
        for (final l in SkyLayer.values)
          FilterChip(
            key: Key('layer-${l.name}'),
            label: Text(layerTitle(l)),
            selected: shown.contains(l),
            // The current set, not the one drawn: two quick taps both count.
            onSelected: (on) => shownLayers.value = on
                ? {...shownLayers.value, l}
                : ({...shownLayers.value}..remove(l)),
          ),
      ],
    ),
  );
}

/// Draws the hub's overlay over a picture shown with BoxFit.contain in the same box.
class SkyOverlayPainter extends CustomPainter {
  SkyOverlayPainter(this.overlay, this.shown);

  final SkyOverlay overlay;
  final Set<SkyLayer> shown;

  static const _lines = Color(0x99A0BBFF);
  static const _names = Color(0xD9CDDAFF);
  static const _grid = Color(0x59C8D2F0);
  static const _star = Color(0xEBEBF0FF);
  static const _planet = Color(0xFFF2C06B);
  static const _sat = Color(0xFF8CE6C8);
  static const _air = Color(0xFFFFC45C);

  @override
  void paint(Canvas canvas, Size size) {
    final scale = math.min(
      size.width / overlay.width,
      size.height / overlay.height,
    );
    final dx = (size.width - overlay.width * scale) / 2;
    final dy = (size.height - overlay.height * scale) / 2;
    Offset at(double x, double y) => Offset(dx + x * scale, dy + y * scale);
    final font = math.max(9.0, math.min(13.0, size.width / 34));

    void polylines(List<List<(double, double)>> lines, Paint paint) {
      for (final line in lines) {
        if (line.length < 2) continue;
        final path = Path()
          ..moveTo(
            at(line[0].$1, line[0].$2).dx,
            at(line[0].$1, line[0].$2).dy,
          );
        for (final p in line.skip(1)) {
          final o = at(p.$1, p.$2);
          path.lineTo(o.dx, o.dy);
        }
        canvas.drawPath(path, paint);
      }
    }

    void text(
      String s,
      Offset o,
      Color colour, {
      double? sizeOf,
      bool centre = false,
      bool bold = false,
    }) {
      final painter = TextPainter(
        text: TextSpan(
          text: s,
          style: TextStyle(
            color: colour,
            fontSize: sizeOf ?? font,
            fontWeight: bold ? FontWeight.w700 : FontWeight.w500,
            shadows: const [Shadow(blurRadius: 3, color: Color(0xCC000000))],
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      final off = centre
          ? o - Offset(painter.width / 2, painter.height / 2)
          : o - Offset(0, painter.height / 2);
      painter.paint(canvas, off);
    }

    Paint stroke(Color c, double w) => Paint()
      ..color = c
      ..style = PaintingStyle.stroke
      ..strokeWidth = w
      ..strokeCap = StrokeCap.round;

    if (shown.contains(SkyLayer.grid)) {
      polylines(overlay.grid, stroke(_grid, 1));
      for (final m in overlay.gridLabels) {
        final cardinal = m.text.length == 1;
        text(
          m.text,
          at(m.x, m.y),
          cardinal ? Sky.accent : _grid.withAlpha(220),
          centre: cardinal,
          bold: cardinal,
          sizeOf: cardinal ? font * 1.3 : font * 0.9,
        );
      }
    }
    if (shown.contains(SkyLayer.constellations)) {
      polylines(overlay.lines, stroke(_lines, 1.2));
      for (final m in overlay.names) {
        text(m.text, at(m.x, m.y), _names, centre: true);
      }
      if (!shown.contains(SkyLayer.grid)) {
        for (final m in overlay.cardinals) {
          text(
            m.text,
            at(m.x, m.y),
            Sky.accent,
            centre: true,
            bold: true,
            sizeOf: font * 1.3,
          );
        }
      }
    }
    if (shown.contains(SkyLayer.stars)) {
      for (final m in overlay.stars) {
        final o = at(m.x, m.y);
        canvas.drawCircle(o, 4, stroke(_star, 1.2));
        text(m.text, o + const Offset(7, 0), _star);
      }
    }
    if (shown.contains(SkyLayer.planets)) {
      for (final m in overlay.planets) {
        final o = at(m.x, m.y);
        canvas.drawCircle(o, 4.5, Paint()..color = _planet);
        text(m.text, o + const Offset(8, 0), _planet);
      }
    }
    void moving(List<SkyMark> marks, Color c) {
      for (final m in marks) {
        if (m.trail.length > 1) {
          polylines([m.trail], stroke(c.withAlpha(170), 1.5));
        }
        final o = at(m.x, m.y);
        canvas.drawCircle(o, 3.5, Paint()..color = c);
        text(m.text, o + const Offset(7, -8), c);
      }
    }

    if (shown.contains(SkyLayer.aircraft)) moving(overlay.aircraft, _air);
    if (shown.contains(SkyLayer.satellites)) moving(overlay.satellites, _sat);
  }

  @override
  bool shouldRepaint(SkyOverlayPainter old) =>
      old.overlay != overlay || old.shown != shown;
}

// ---- Orientation and "Himmel jetzt" (drafts 2 and 4) ----------------------------------

double? _num(Object? v) => v is num && v.isFinite ? v.toDouble() : null;

/// How well the camera knows its orientation (status.orientation), like the hub's line.
class OrientationLine extends StatelessWidget {
  const OrientationLine({super.key, required this.status});

  final Map<String, dynamic> status;

  @override
  Widget build(BuildContext context) {
    final o = status['orientation'];
    if (o is! Map<String, dynamic>) return const SizedBox.shrink();
    final north = _num(o['north_deg']);
    if (north == null) return const SizedBox.shrink();
    final rms = _num(o['rms_deg']);
    final stars = o['stars'];
    final solved = DateTime.tryParse(o['solved_at'] as String? ?? '');
    final (label, colour) = rms == null || rms <= 0.4
        ? (rms != null && rms <= 0.2
              ? (tr('gut eingemessen', 'well calibrated'), Sky.aurora)
              : (tr('brauchbar eingemessen', 'usably calibrated'), Sky.warn))
        : (tr('ungenau eingemessen', 'roughly calibrated'), Sky.danger);
    final small = Theme.of(context).textTheme.bodySmall;
    String deg(double v) => appEnglish
        ? v.toStringAsFixed(2)
        : v.toStringAsFixed(2).replaceAll('.', ',');
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
      child: Wrap(
        key: const Key('orientation-line'),
        spacing: 10,
        runSpacing: 4,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          DecoratedBox(
            decoration: BoxDecoration(
              color: colour.withAlpha(36),
              borderRadius: BorderRadius.circular(99),
            ),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 2),
              child: Text(
                label,
                style: small?.copyWith(
                  color: colour,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ),
          if (stars is int)
            Text(tr('$stars Sterne', '$stars stars'), style: small),
          if (rms != null)
            Text(
              tr('Abweichung ${deg(rms)}°', 'off by ${deg(rms)}°'),
              style: small,
            ),
          Text(
            tr(
              'Norden bei ${north.round() % 360}°',
              'north at ${north.round() % 360}°',
            ),
            style: small,
          ),
          if (solved != null) Text(formatAge(solved), style: small),
        ],
      ),
    );
  }
}

/// Image angle (clockwise from up) of an azimuth, as SPEC §6.3 orientation says.
double imageAngle(double azimuth, double north, bool mirrored) =>
    mirrored ? north - azimuth : north + azimuth;

class _CloudMapPainter extends CustomPainter {
  _CloudMapPainter(this.cells, this.north, this.mirrored);

  final List<double?> cells;
  final double north;
  final bool mirrored;

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final r = math.min(size.width, size.height) / 2 * 0.8;
    const radii = [(1.0, 0.66), (0.66, 0.34), (0.34, 0.0)];
    final line = Paint()
      ..color = Sky.line
      ..style = PaintingStyle.stroke;
    for (var i = 0; i < 24; i++) {
      final band = i ~/ 8;
      final sector = i % 8;
      final v = cells[i];
      final (outer, inner) = radii[band];
      final a0 = imageAngle(sector * 45.0 - 22.5, north, mirrored);
      final a1 = imageAngle(sector * 45.0 + 22.5, north, mirrored);
      // Canvas angles: 0 = right, clockwise; image angles: 0 = up, clockwise.
      final start = (math.min(a0, a1) - 90) * math.pi / 180;
      const sweep = 45 * math.pi / 180;
      final path = Path()
        ..arcTo(
          Rect.fromCircle(center: c, radius: r * outer),
          start,
          sweep,
          true,
        );
      if (inner > 0) {
        path.arcTo(
          Rect.fromCircle(center: c, radius: r * inner),
          start + sweep,
          -sweep,
          false,
        );
      } else {
        path.lineTo(c.dx, c.dy);
      }
      path.close();
      if (v != null) {
        canvas.drawPath(
          path,
          Paint()..color = Sky.text.withAlpha((10 + 180 * v).round()),
        );
      }
      canvas.drawPath(path, line);
    }
    for (final (letter, az) in const [
      ('N', 0.0),
      ('O', 90.0),
      ('S', 180.0),
      ('W', 270.0),
    ]) {
      final a = imageAngle(az, north, mirrored) * math.pi / 180;
      final o = c + Offset(math.sin(a), -math.cos(a)) * (r + 9);
      final p = TextPainter(
        text: TextSpan(
          text: appEnglish && letter == 'O' ? 'E' : letter,
          style: const TextStyle(
            color: Sky.text2,
            fontSize: 10,
            fontWeight: FontWeight.w700,
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      p.paint(canvas, o - Offset(p.width / 2, p.height / 2));
    }
  }

  @override
  bool shouldRepaint(_CloudMapPainter old) =>
      old.cells != cells || old.north != north || old.mirrored != mirrored;
}

/// "Himmel jetzt": the camera's own measurement of the sky (status.sky).
class SkyNowCard extends StatelessWidget {
  const SkyNowCard({super.key, required this.status});

  final Map<String, dynamic> status;

  @override
  Widget build(BuildContext context) {
    final sky = status['sky'];
    if (sky is! Map<String, dynamic>) return const SizedBox.shrink();
    final stars = sky['stars'];
    final cover = _num(sky['cloud_cover']);
    final sqm = _num(sky['sqm_mag']);
    final transparency = _num(sky['transparency']);
    final raw = sky['cloud_map'];
    final cells = raw is List && raw.length == 24
        ? [for (final v in raw) _num(v)]
        : null;
    final o = status['orientation'];
    final north = o is Map<String, dynamic> ? _num(o['north_deg']) : null;
    final mirrored = o is Map<String, dynamic> ? o['mirrored'] != false : true;
    final at = DateTime.tryParse(sky['at'] as String? ?? '');
    String dec(double v, int places) => appEnglish
        ? v.toStringAsFixed(places)
        : v.toStringAsFixed(places).replaceAll('.', ',');
    final tiles = <Widget>[
      if (stars is int)
        _tile(
          context,
          tr('Sterne gesehen', 'Stars seen'),
          '$stars',
          tr('im neuesten Nachtbild', 'in the newest night frame'),
        ),
      if (cover != null)
        _tile(
          context,
          tr('Bewölkung', 'Clouds'),
          '${(cover * 100).round()} %',
          stars is int
              ? tr('aus den Sternen', 'from the stars')
              : tr('aus der Himmelsfarbe', 'from the sky colour'),
        ),
      if (sqm != null)
        _tile(
          context,
          tr('Himmelshelligkeit', 'Sky brightness'),
          dec(sqm, 1),
          tr('mag/″², ungefähr', 'mag/″², roughly'),
        ),
      if (transparency != null)
        _tile(
          context,
          tr('Transparenz', 'Transparency'),
          dec(transparency, 2),
          tr('mag gedämpft über 40°', 'mag dimmed above 40°'),
        ),
    ];
    final hasMap = cells != null && cells.any((v) => v != null);
    if (tiles.isEmpty && !hasMap) return const SizedBox.shrink();
    final theme = Theme.of(context);
    return Card(
      key: const Key('sky-now'),
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    tr('Himmel jetzt', 'Sky now'),
                    style: theme.textTheme.titleMedium,
                  ),
                ),
                if (at != null)
                  Text(formatAge(at), style: theme.textTheme.bodySmall),
              ],
            ),
            const SizedBox(height: 10),
            Wrap(spacing: 10, runSpacing: 10, children: tiles),
            if (hasMap) ...[
              const SizedBox(height: 12),
              Row(
                children: [
                  SizedBox(
                    width: 120,
                    height: 120,
                    child: CustomPaint(
                      key: const Key('cloud-map'),
                      painter: _CloudMapPainter(cells, north ?? 0, mirrored),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      tr(
                        'Wolken je Richtung, gedreht wie das Bild; hell = Wolken.',
                        'Clouds by direction, turned like the picture; bright = clouds.',
                      ),
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _tile(BuildContext context, String label, String value, String note) {
    final theme = Theme.of(context);
    return Container(
      width: 140,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Sky.surface2,
        border: Border.all(color: Sky.line),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label.toUpperCase(),
            style: theme.textTheme.labelSmall?.copyWith(
              color: Sky.text2,
              letterSpacing: 1,
            ),
          ),
          Text(
            value,
            style: const TextStyle(
              fontFamily: Sky.display,
              fontSize: 24,
              fontWeight: FontWeight.w700,
            ),
          ),
          Text(note, style: theme.textTheme.bodySmall),
        ],
      ),
    );
  }
}

// ---- Satellites (draft 5) -------------------------------------------------------------

String _compass(double az) {
  const de = ['N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW'];
  const en = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  final i = ((az % 360) / 45).round() % 8;
  return appEnglish ? en[i] : de[i];
}

String _clock(DateTime t) {
  final l = t.toLocal();
  return '${l.hour.toString().padLeft(2, '0')}:${l.minute.toString().padLeft(2, '0')}';
}

class SatellitesCard extends StatefulWidget {
  const SatellitesCard({
    super.key,
    required this.client,
    required this.cameraId,
  });

  final HubClient client;
  final String cameraId;

  @override
  State<SatellitesCard> createState() => _SatellitesCardState();
}

class _SatellitesCardState extends State<SatellitesCard> {
  String _filter = 'jetzt';
  SatelliteView? _view;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final v = await widget.client.satellites(widget.cameraId, _filter);
      if (mounted) {
        setState(() {
          _view = v;
          _failed = false;
        });
      }
    } on Object {
      if (mounted) setState(() => _failed = true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final v = _view;
    if (_failed && v == null) return const SizedBox.shrink(); // older hub
    Widget tag(String s, Color c) => Container(
      margin: const EdgeInsets.only(right: 5, top: 4),
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
        color: c.withAlpha(40),
        borderRadius: BorderRadius.circular(5),
      ),
      child: Text(
        s,
        style: TextStyle(fontSize: 11, color: c, fontWeight: FontWeight.w600),
      ),
    );
    final List<Widget> body;
    if (v == null) {
      body = [const LinearProgressIndicator()];
    } else if (!v.located) {
      body = [
        Text(
          tr(
            'Die Kamera hat ihren Standort noch nicht gemeldet.',
            'The camera has not reported its location yet.',
          ),
        ),
      ];
    } else if (!v.loaded) {
      body = [
        Text(
          tr(
            'Die Bahndaten der Satelliten sind gerade nicht geladen.',
            'The satellites’ orbits are not loaded right now.',
          ),
        ),
      ];
    } else if (_filter == 'nacht') {
      body = v.passes.isEmpty
          ? [
              Text(
                tr(
                  'Heute Nacht kommt kein heller Satellit höher als 30°.',
                  'No bright satellite climbs above 30° tonight.',
                ),
              ),
            ]
          : [
              for (final p in v.passes)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(p.name),
                  subtitle: Text(
                    tr(
                      '${_clock(p.start)}–${_clock(p.end)} · bis ${p.topAltitude.round()}° ${_compass(p.topAzimuth)} · von ${_compass(p.startAzimuth)} nach ${_compass(p.endAzimuth)}',
                      '${_clock(p.start)}–${_clock(p.end)} · up to ${p.topAltitude.round()}° ${_compass(p.topAzimuth)} · from ${_compass(p.startAzimuth)} to ${_compass(p.endAzimuth)}',
                    ),
                  ),
                ),
            ];
    } else {
      body = v.now.isEmpty
          ? [
              Text(
                _filter == 'jetzt'
                    ? tr(
                        'Gerade ist kein heller Satellit mit bloßem Auge zu sehen.',
                        'No bright satellite is visible to the naked eye right now.',
                      )
                    : tr(
                        'Gerade ist kein heller Satellit über dem Horizont.',
                        'No bright satellite is above the horizon right now.',
                      ),
              ),
            ]
          : [
              for (final s in v.now.take(25))
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              s.name,
                              style: theme.textTheme.bodyLarge,
                            ),
                          ),
                          Text(
                            '${s.altitude.round()}° ${_compass(s.azimuth)} · ${s.heightKm.round()} km',
                            style: theme.textTheme.bodySmall,
                          ),
                        ],
                      ),
                      Wrap(
                        children: [
                          if (s.nakedEye)
                            tag(tr('mit bloßem Auge', 'naked eye'), Sky.accent),
                          s.sunlit
                              ? tag(tr('sonnenbeschienen', 'sunlit'), Sky.warn)
                              : tag(
                                  tr('im Erdschatten', 'in Earth’s shadow'),
                                  Sky.text2,
                                ),
                          if (s.inPicture)
                            tag(tr('im Bild', 'in the picture'), Sky.aurora),
                        ],
                      ),
                    ],
                  ),
                ),
            ];
    }
    return Card(
      key: const Key('satellites'),
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              tr('Satelliten', 'Satellites'),
              style: theme.textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            SegmentedButton<String>(
              showSelectedIcon: false,
              segments: [
                ButtonSegment(
                  value: 'jetzt',
                  label: Text(tr('Sichtbar', 'Visible')),
                ),
                ButtonSegment(
                  value: 'oben',
                  label: Text(tr('Über Horizont', 'Up')),
                ),
                ButtonSegment(
                  value: 'nacht',
                  label: Text(tr('Diese Nacht', 'This night')),
                ),
              ],
              selected: {_filter},
              onSelectionChanged: (s) {
                setState(() {
                  _filter = s.first;
                  _view = null;
                });
                _load();
              },
            ),
            const SizedBox(height: 8),
            ...body,
            const SizedBox(height: 6),
            Text(
              tr(
                'Die etwa 150 hellsten Objekte. „Mit bloßem Auge“ ist eine Schätzung.',
                'The about 150 brightest objects. “Naked eye” is an estimate.',
              ),
              style: theme.textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}
