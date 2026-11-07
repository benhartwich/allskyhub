// SharedSky for everyone who takes part (SPEC §10.6; Benjamin's draft of 2026-11-07):
// "Das Netz" from "Entdecken", and on the account's own station "Im Netz", "Dein Himmel
// im Vergleich" and the four choices of what it shows.

import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import '../design.dart';
import '../l10n.dart';
import 'globe.dart';
import 'public_camera_screen.dart';

String _sqm(double v) => decimal(v, 2);

String _software(String? software) => switch (software) {
  'indi-allsky' => 'indi-allsky',
  'image-url' || 'allsky' => 'Allsky',
  _ => tr('eigene Software', 'own software'),
};

/// The card on "Entdecken" that leads to [NetworkScreen].
class NetworkCard extends StatelessWidget {
  const NetworkCard({super.key, required this.client, required this.view});

  final HubClient client;
  final NetworkView view;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        key: const Key('network-card'),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => NetworkScreen(client: client, first: view),
          ),
        ),
        child: Container(
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              colors: [Color(0xF2243766), Color(0xF233295A)],
            ),
          ),
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'SHAREDSKY',
                style: theme.textTheme.labelSmall?.copyWith(color: Sky.aurora),
              ),
              const SizedBox(height: 4),
              Text(
                tr('Das Netz heute Nacht', 'The network tonight'),
                style: theme.textTheme.titleLarge,
              ),
              const SizedBox(height: 4),
              Text(
                tr(
                  '${view.active} Kameras aktiv · ${view.meteorsTonight} Meteore · ${view.tonight.length} Feuerkugeln vermessen',
                  '${view.active} cameras active · ${view.meteorsTonight} meteors · ${view.tonight.length} fireballs measured',
                ),
              ),
              const SizedBox(height: 8),
              Text(
                tr('Ansehen ›', 'Open ›'),
                style: theme.textTheme.labelLarge,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Tile extends StatelessWidget {
  const _Tile(this.value, this.label);

  final String value;
  final String label;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              value,
              style: theme.textTheme.headlineSmall?.copyWith(
                fontFamily: Sky.display,
                fontFeatures: const [FontFeature.tabularFigures()],
              ),
            ),
            Text(label, style: theme.textTheme.bodySmall),
          ],
        ),
      ),
    );
  }
}

Widget _tiles(List<Widget> tiles) => LayoutBuilder(
  builder: (context, box) {
    final columns = box.maxWidth > 520 ? 4 : 2;
    final width = (box.maxWidth - 10 * (columns - 1)) / columns;
    return Wrap(
      spacing: 10,
      runSpacing: 10,
      children: [for (final t in tiles) SizedBox(width: width, child: t)],
    );
  },
);

Widget _heading(BuildContext context, String text) => Padding(
  padding: const EdgeInsets.fromLTRB(0, 20, 0, 8),
  child: Text(text, style: Theme.of(context).textTheme.titleMedium),
);

Widget _kindChip(String kind, String? software) => Chip(
  visualDensity: VisualDensity.compact,
  materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
  label: Text(
    kind == 'station' ? _software(software) : 'allskyhub',
    style: const TextStyle(fontSize: 11),
  ),
  side: BorderSide(color: kind == 'station' ? Sky.accent2 : Sky.accent),
);

/// "Das Netz": what the cameras and stations measure together, and who takes part.
class NetworkScreen extends StatefulWidget {
  const NetworkScreen({super.key, required this.client, this.first});

  final HubClient client;

  /// What "Entdecken" already loaded, shown until the next refresh.
  final NetworkView? first;

  @override
  State<NetworkScreen> createState() => _NetworkScreenState();
}

class _NetworkScreenState extends State<NetworkScreen> {
  late Future<NetworkView?> _view = widget.first != null
      ? Future.value(widget.first)
      : widget.client.networkView();

  Future<void> _reload() async {
    final next = widget.client.networkView();
    setState(() => _view = next);
    await next;
  }

  void _open(String slug, String title) => Navigator.of(context).push(
    MaterialPageRoute<void>(
      builder: (_) =>
          PublicCameraScreen(client: widget.client, slug: slug, title: title),
    ),
  );

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(tr('Das Netz', 'The network'))),
    body: RefreshIndicator(
      onRefresh: _reload,
      child: FutureBuilder(
        future: _view,
        builder: (context, snapshot) {
          final theme = Theme.of(context);
          if (snapshot.hasError || snapshot.data == null) {
            if (snapshot.connectionState != ConnectionState.done) {
              return const Center(child: CircularProgressIndicator());
            }
            return ListView(
              padding: const EdgeInsets.all(32),
              children: [
                Text(
                  tr(
                    'Das Netz konnte nicht geladen werden.',
                    'Could not load the network.',
                  ),
                  textAlign: TextAlign.center,
                ),
                TextButton(
                  onPressed: _reload,
                  child: Text(tr('Erneut versuchen', 'Try again')),
                ),
              ],
            );
          }
          final v = snapshot.data!;
          final onGlobe = v.points;
          return ListView(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
            children: [
              Text(
                tr(
                  '${v.cameras + v.stations} Kameras messen mit: ${v.cameras} allskyhub-Kameras und ${v.stations} SharedSky-Stationen.',
                  '${v.cameras + v.stations} cameras take part: ${v.cameras} allskyhub cameras and ${v.stations} SharedSky stations.',
                ),
              ),
              const SizedBox(height: 12),
              _tiles([
                _Tile('${v.active}', tr('aktiv', 'active')),
                _Tile(
                  '${v.meteorsTonight}',
                  tr('Meteore heute Nacht', 'meteors tonight'),
                ),
                _Tile(
                  '${v.fireballsMonth}',
                  tr('Feuerkugeln, 30 Tage', 'fireballs, 30 days'),
                ),
                _Tile(
                  v.darkest.isEmpty ? '–' : _sqm(v.darkest.first.sqm),
                  tr('mag/″² dunkelster Himmel', 'mag/″² darkest sky'),
                ),
              ]),
              SizedBox(
                height: 300,
                child: Globe(
                  key: const Key('network-globe'),
                  markers: [
                    for (final p in onGlobe)
                      GlobeMarker(lat: p.lat, lon: p.lon, night: p.active),
                  ],
                  onTap: (i) {
                    final p = onGlobe[i];
                    if (p.slug case final slug?) _open(slug, p.title);
                  },
                ),
              ),
              Text(
                tr(
                  'Grün: gerade aktiv. Orte auf etwa 10 km gerundet.',
                  'Green: active now. Places rounded to about 10 km.',
                ),
                style: theme.textTheme.bodySmall,
              ),
              _heading(context, tr('Heute Nacht', 'Tonight')),
              Card(
                margin: EdgeInsets.zero,
                child: Column(
                  children: [
                    for (final f in v.tonight)
                      ListTile(
                        leading: const Icon(
                          Icons.auto_awesome,
                          color: Sky.warn,
                        ),
                        title: Text(
                          tr(
                            'Feuerkugel ${TimeOfDay.fromDateTime(f.at.toLocal()).format(context)}',
                            'Fireball ${TimeOfDay.fromDateTime(f.at.toLocal()).format(context)}',
                          ),
                        ),
                        subtitle: Text(
                          '${f.highKm.round()} → ${f.lowKm.round()} km · ${f.cameras.join(', ')}',
                        ),
                      ),
                    for (final s in v.showersTonight.take(4))
                      ListTile(
                        title: Text(s.name),
                        trailing: Text(
                          '${s.count}',
                          style: theme.textTheme.titleMedium,
                        ),
                      ),
                    if (v.tonight.isEmpty && v.showersTonight.isEmpty)
                      ListTile(
                        title: Text(
                          tr(
                            'Noch keine Meteore heute Nacht.',
                            'No meteors tonight yet.',
                          ),
                        ),
                      ),
                  ],
                ),
              ),
              if (v.darkest.isNotEmpty) ...[
                _heading(
                  context,
                  tr(
                    'Himmelshelligkeit letzte Nacht',
                    'Sky brightness last night',
                  ),
                ),
                Card(
                  margin: EdgeInsets.zero,
                  child: Column(
                    children: [
                      for (final r in v.darkest)
                        ListTile(
                          onTap: r.slug == null
                              ? null
                              : () => _open(r.slug!, r.name),
                          title: Text(r.name),
                          subtitle: Padding(
                            padding: const EdgeInsets.only(top: 6),
                            child: LinearProgressIndicator(
                              value: ((r.sqm - 18) / 4).clamp(0.02, 1.0),
                              backgroundColor: const Color(0xFF1F2A55),
                              color: Sky.accent,
                              minHeight: 5,
                              borderRadius: BorderRadius.circular(3),
                            ),
                          ),
                          trailing: Text(
                            _sqm(r.sqm),
                            style: theme.textTheme.titleMedium,
                          ),
                        ),
                    ],
                  ),
                ),
              ],
              _heading(context, tr('Wer mitmacht', 'Who takes part')),
              Card(
                margin: EdgeInsets.zero,
                child: Column(
                  children: [
                    for (final m in v.members)
                      ListTile(
                        key: Key('member-${m.name}'),
                        onTap: m.slug == null
                            ? null
                            : () => _open(m.slug!, m.name),
                        title: Row(
                          children: [
                            Flexible(
                              child: Text(
                                m.name,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                            const SizedBox(width: 8),
                            _kindChip(m.kind, m.software),
                          ],
                        ),
                        subtitle: Text(
                          tr(
                            '${m.fireballs} Feuerkugeln, ${m.meteors} Meteore',
                            '${m.fireballs} fireballs, ${m.meteors} meteors',
                          ),
                        ),
                        trailing: Icon(
                          Icons.circle,
                          size: 10,
                          color: m.active ? Sky.aurora : Sky.text2,
                        ),
                      ),
                    if (v.members.isEmpty)
                      ListTile(
                        title: Text(
                          tr('Noch niemand öffentlich.', 'No one public yet.'),
                        ),
                      ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              Text(
                tr(
                  'Messwerte unter CC BY 4.0, mit Nennung der Stationen. Meteore von SharedSky-Stationen laufen noch als Probe und gehen nicht in gemeinsame Bahnen ein.',
                  'Measurements under CC BY 4.0, crediting the stations. Meteors from SharedSky stations are still a trial and do not go into joint paths.',
                ),
                style: theme.textTheme.bodySmall,
              ),
            ],
          );
        },
      ),
    ),
  );
}

/// "Im Netz": what the account's camera or station adds.
class NetworkPartCard extends StatelessWidget {
  const NetworkPartCard({super.key, required this.part});

  final NetworkPart part;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              tr('Im Netz', 'In the network'),
              style: theme.textTheme.titleMedium,
            ),
            const SizedBox(height: 4),
            Text(
              tr(
                '${part.fireballs} Feuerkugeln mitvermessen · ${part.meteors} Meteore für die Ströme',
                '${part.fireballs} fireballs measured together · ${part.meteors} meteors for the showers',
              ),
            ),
            for (final f in part.recent) ...[
              const SizedBox(height: 8),
              Text(
                tr(
                  'Feuerkugel ${dayMonth(f.at.toLocal())}${f.cameras.isEmpty ? '' : ', mit ${f.cameras.join(', ')}'}',
                  'Fireball ${dayMonth(f.at.toLocal())}${f.cameras.isEmpty ? '' : ', with ${f.cameras.join(', ')}'}',
                ),
                style: theme.textTheme.bodySmall,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// "Dein Himmel im Vergleich": 30 nights of the camera's darkest reading next to the
/// range of its neighbours, on one scale for marks and labels.
class SkyComparisonCard extends StatelessWidget {
  const SkyComparisonCard({
    super.key,
    required this.comparison,
    required this.name,
  });

  final SkyComparison comparison;
  final String name;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final d = comparison.difference;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              tr('Dein Himmel im Vergleich', 'Your sky compared'),
              style: theme.textTheme.titleMedium,
            ),
            Text(
              tr(
                'Letzte 30 Nächte, mag/″², höher ist dunkler',
                'Last 30 nights, mag/″², higher is darker',
              ),
              style: theme.textTheme.bodySmall,
            ),
            const SizedBox(height: 12),
            SizedBox(
              height: 180,
              width: double.infinity,
              child: CustomPaint(
                key: const Key('sky-comparison'),
                painter: _ComparisonPainter(
                  comparison,
                  theme.textTheme.bodySmall ?? const TextStyle(fontSize: 11),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 12,
              runSpacing: 4,
              children: [
                _legend(Sky.aurora, name, theme),
                _legend(
                  Sky.accent.withValues(alpha: .45),
                  tr(
                    'Spanne der ${comparison.neighbours} Nachbarn bis 150 km',
                    'range of the ${comparison.neighbours} neighbours within 150 km',
                  ),
                  theme,
                ),
              ],
            ),
            if (d != null) ...[
              const SizedBox(height: 8),
              Text(
                d > 0.05
                    ? tr(
                        'Im Mittel ${decimal(d, 1)} mag/″² dunkler als deine Nachbarn.',
                        'On average ${decimal(d, 1)} mag/″² darker than your neighbours.',
                      )
                    : d < -0.05
                    ? tr(
                        'Im Mittel ${decimal(-d, 1)} mag/″² heller als deine Nachbarn.',
                        'On average ${decimal(-d, 1)} mag/″² brighter than your neighbours.',
                      )
                    : tr(
                        'Etwa so dunkel wie deine Nachbarn.',
                        'About as dark as your neighbours.',
                      ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _legend(Color color, String text, ThemeData theme) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      Icon(Icons.circle, size: 10, color: color),
      const SizedBox(width: 6),
      Flexible(child: Text(text, style: theme.textTheme.bodySmall)),
    ],
  );
}

class _ComparisonPainter extends CustomPainter {
  _ComparisonPainter(this.data, this.label);

  final SkyComparison data;
  final TextStyle label;

  @override
  void paint(Canvas canvas, Size size) {
    const left = 40.0, bottom = 20.0, top = 6.0, right = 6.0;
    final values = [
      for (final n in data.nights) ...[n.own, n.low, n.high],
    ].whereType<double>().toList();
    if (values.isEmpty) return;
    final lo = (values.reduce(math.min) * 2).floor() / 2;
    final hi = (values.reduce(math.max) * 2).floor() / 2 + 0.5;
    final count = math.max(data.nights.length - 1, 1);
    double x(int i) => left + i * (size.width - left - right) / count;
    double y(double v) =>
        top + (hi - v) / (hi - lo) * (size.height - top - bottom);

    final grid = Paint()
      ..color = Sky.line
      ..strokeWidth = 1;
    for (var v = lo; v <= hi + 1e-9; v += 0.5) {
      canvas.drawLine(
        Offset(left, y(v)),
        Offset(size.width - right, y(v)),
        grid,
      );
      _text(canvas, decimal(v, 1), Offset(left - 6, y(v)), right: true);
    }
    final range = Paint()..color = Sky.accent.withValues(alpha: .45);
    for (var i = 0; i < data.nights.length; i++) {
      final n = data.nights[i];
      if (n.low == null || n.high == null) continue;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTRB(
            x(i) - 3,
            y(n.high!),
            x(i) + 3,
            math.max(y(n.low!), y(n.high!) + 2),
          ),
          const Radius.circular(3),
        ),
        range,
      );
    }
    final line = Paint()
      ..color = Sky.aurora
      ..strokeWidth = 2.5
      ..style = PaintingStyle.stroke
      ..strokeJoin = StrokeJoin.round;
    final path = Path();
    var pen = false;
    Offset? last;
    for (var i = 0; i < data.nights.length; i++) {
      final own = data.nights[i].own;
      if (own == null) {
        pen = false;
        continue;
      }
      final p = Offset(x(i), y(own));
      pen ? path.lineTo(p.dx, p.dy) : path.moveTo(p.dx, p.dy);
      pen = true;
      last = p;
    }
    canvas.drawPath(path, line);
    if (last != null) canvas.drawCircle(last, 4.5, Paint()..color = Sky.aurora);
    for (final i in {0, data.nights.length ~/ 2, data.nights.length - 1}) {
      final id = data.nights[i].nightId;
      final day = DateTime(
        int.parse(id.substring(0, 4)),
        int.parse(id.substring(4, 6)),
        int.parse(id.substring(6, 8)),
      );
      _text(
        canvas,
        dayMonth(day),
        Offset(x(i), size.height - bottom + 4),
        centre: true,
      );
    }
  }

  void _text(
    Canvas canvas,
    String text,
    Offset at, {
    bool right = false,
    bool centre = false,
  }) {
    final painter = TextPainter(
      text: TextSpan(text: text, style: label),
      textDirection: TextDirection.ltr,
    )..layout();
    final dx = right
        ? at.dx - painter.width
        : centre
        ? at.dx - painter.width / 2
        : at.dx;
    final dy = right ? at.dy - painter.height / 2 : at.dy;
    painter.paint(canvas, Offset(dx, dy));
  }

  @override
  bool shouldRepaint(_ComparisonPainter old) => old.data != data;
}

/// The four choices of what a station shows (SPEC §10.6), saved as they change.
class StationVisibilityCard extends StatelessWidget {
  const StationVisibilityCard({
    super.key,
    required this.visibility,
    required this.onChanged,
    this.publicUrl,
    this.publicFrom,
    this.waiting = false,
  });

  final StationVisibility visibility;
  final ValueChanged<StationVisibility>? onChanged;
  final String? publicUrl;
  final DateTime? publicFrom;
  final bool waiting;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
              child: Text(
                tr('Öffentlich', 'Public'),
                style: theme.textTheme.titleMedium,
              ),
            ),
            if (waiting)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                child: Text(
                  publicFrom != null
                      ? tr(
                          'Deine Station war schon dabei, bevor es diese Auswahl gab. Sie gilt für sie ab ${dayMonth(publicFrom!.toLocal())}, wenn du nichts anderes einstellst.',
                          'Your station joined before these choices existed. They hold for it from ${dayMonth(publicFrom!.toLocal())}, unless you set something else.',
                        )
                      : tr(
                          'Deine Station war schon dabei, bevor es diese Auswahl gab. Sie gilt für sie eine Woche nach unserer Mail an dich, wenn du nichts anderes einstellst.',
                          'Your station joined before these choices existed. They hold for it a week after our e-mail to you, unless you set something else.',
                        ),
                  style: theme.textTheme.bodySmall?.copyWith(color: Sky.warn),
                ),
              )
            else if (publicUrl != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                child: SelectableText(
                  publicUrl!,
                  style: theme.textTheme.bodySmall,
                ),
              ),
            StationVisibilitySwitches(
              visibility: visibility,
              onChanged: onChanged,
            ),
          ],
        ),
      ),
    );
  }
}

/// The switches alone, also when connecting.
class StationVisibilitySwitches extends StatelessWidget {
  const StationVisibilitySwitches({
    super.key,
    required this.visibility,
    required this.onChanged,
  });

  final StationVisibility visibility;
  final ValueChanged<StationVisibility>? onChanged;

  @override
  Widget build(BuildContext context) {
    final v = visibility;
    final change = onChanged;
    Widget row(
      String key,
      bool value,
      String title,
      String text,
      StationVisibility Function(bool) next,
    ) => SwitchListTile(
      key: Key('visibility-$key'),
      value: value,
      onChanged: change == null ? null : (on) => change(next(on)),
      title: Text(title),
      subtitle: Text(text),
    );
    return Column(
      children: [
        row(
          'named',
          v.named,
          tr('Mit Namen nennen', 'Name it'),
          tr(
            'Sonst steht in Ergebnissen „eine Station im Netz“.',
            'Otherwise results say "a station in the network".',
          ),
          (on) => v.copyWith(named: on),
        ),
        row(
          'map',
          v.onMap,
          tr('Auf der Netzkarte', "On the network's map"),
          tr(
            'Der Ort auf etwa 10 km gerundet.',
            'The place rounded to about 10 km.',
          ),
          (on) => v.copyWith(onMap: on),
        ),
        row(
          'page',
          v.page,
          tr('Öffentliche Seite', 'Public page'),
          tr(
            'Himmel, Meteore und was die Station zum Netz beiträgt.',
            'Sky, meteors and what the station adds to the network.',
          ),
          (on) => v.copyWith(page: on),
        ),
        row(
          'images',
          v.images,
          tr('Bilder zeigen', 'Show images'),
          tr(
            'Letztes Bild und Meteorbilder auf der öffentlichen Seite.',
            'The latest image and meteor images on the public page.',
          ),
          (on) => v.copyWith(images: on),
        ),
      ],
    );
  }
}
