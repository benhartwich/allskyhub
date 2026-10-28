// "Himmelshelligkeit melden" (stage 4): a person compares a constellation with star charts
// and reports the faintest stars seen; the hub turns that into the sky brightness and a
// point on the light-pollution map (api/app_reports.py). Needs an account (a reporter
// account from /melden/konto, or a camera owner's). Red and dark, so the eyes stay
// adapted to the night.

import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/hub_client.dart';
import '../design.dart';
import '../l10n.dart';
import '../platform/location.dart';
import 'join_screen.dart' show SignInScope;
import 'sky_view_screen.dart';

/// Night red: keeps the eyes adapted (bortle's red mode uses the same colour).
const nightRed = Color(0xFFFF5A4E);
const _adapt = Duration(minutes: 15);

/// Everything in shades of red, like a red torch.
Widget redNight(Widget child) => ColorFiltered(
  colorFilter: const ColorFilter.matrix([
    0.3, 0.6, 0.1, 0, 0, //
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0,
    0, 0, 0, 1, 0,
  ]),
  child: child,
);

/// Without an account: what reporting is, and where to get an account.
class ReportGateScreen extends StatelessWidget {
  const ReportGateScreen({super.key, required this.client});

  final HubClient client;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final signIn = SignInScope.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(
          tr('Himmelshelligkeit melden', 'Report the sky brightness'),
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text(
            tr('Wie dunkel ist dein Himmel?', 'How dark is your sky?'),
            style: theme.textTheme.headlineSmall,
          ),
          const SizedBox(height: 10),
          Text(
            tr(
              'Du schaust dir ein Sternbild an und wählst das Bild, das zu deinem Himmel passt. Daraus wird ein Punkt auf der Lichtverschmutzungs-Karte, neben den Messungen der Kameras.',
              'You look at a constellation and pick the picture that matches your sky. It becomes a point on the light pollution map, next to the cameras’ measurements.',
            ),
          ),
          const SizedBox(height: 14),
          Card(
            margin: EdgeInsets.zero,
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Text(
                tr(
                  'Zum Melden brauchst du ein kostenloses Melder-Konto, damit die Karte nicht mit Unsinn gefüllt wird. Es hat nur deine E-Mail-Adresse; dein Name erscheint nirgends.',
                  'To report you need a free reporter account, so the map is not filled with nonsense. It holds only your email address; your name appears nowhere.',
                ),
              ),
            ),
          ),
          const SizedBox(height: 24),
          FilledButton(
            key: const Key('report-signup'),
            onPressed: () => launchUrl(
              client.site('/melden/konto'),
              mode: LaunchMode.externalApplication,
            ),
            child: Text(
              tr('Melder-Konto anlegen', 'Create a reporter account'),
            ),
          ),
          if (signIn != null)
            TextButton(
              key: const Key('report-sign-in'),
              onPressed: signIn,
              child: Text(
                tr(
                  'Ich habe ein Konto · Anmelden',
                  'I have an account · Sign in',
                ),
              ),
            ),
        ],
      ),
    );
  }
}

enum _Step { prepare, compare, sent }

class ReportScreen extends StatefulWidget {
  const ReportScreen({
    super.key,
    required this.client,
    required this.location,
    this.started,
  });

  final HubClient client;
  final LocationSource location;

  /// When dark adaptation started (tests); now by default.
  final DateTime? started;

  @override
  State<ReportScreen> createState() => _ReportScreenState();
}

class _ReportScreenState extends State<ReportScreen> {
  late final DateTime _started = widget.started ?? DateTime.now();
  Timer? _tick;
  _Step _step = _Step.prepare;
  ({double lat, double lon})? _place;
  ReportChart? _chart;
  String? _error;
  double? _limit;
  bool _clouds = false;
  bool _sending = false;
  Map<String, dynamic>? _result;

  @override
  void initState() {
    super.initState();
    _load();
    _tick = Timer.periodic(const Duration(seconds: 5), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final found = await widget.location.current();
    final at = found.location;
    if (at == null) {
      if (mounted) {
        setState(
          () => _error = tr(
            'Zum Melden braucht die App deinen ungefähren Standort: Die Meldung gilt für den Himmel dort, wo du stehst.',
            'To report, the app needs your rough location: a report is about the sky where you stand.',
          ),
        );
      }
      return;
    }
    _place = (lat: at.latitude, lon: at.longitude);
    try {
      final chart = await widget.client.reportChart(at.latitude, at.longitude);
      if (mounted) setState(() => _chart = chart);
    } on HubException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } on Exception {
      if (mounted) {
        setState(
          () => _error = tr(
            'Keine Verbindung zum Hub.',
            'No connection to the hub.',
          ),
        );
      }
    }
  }

  Future<void> _send() async {
    final chart = _chart, place = _place, limit = _limit;
    if (chart == null || place == null || limit == null) return;
    setState(() => _sending = true);
    try {
      final result = await widget.client.sendReport(
        lat: place.lat,
        lon: place.lon,
        constellation: chart.key!,
        limit: limit,
        clouds: _clouds,
      );
      if (mounted) {
        setState(() {
          _result = result;
          _step = _Step.sent;
        });
      }
    } on HubException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) => redNight(
    Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        title: Text(
          tr('Himmelshelligkeit melden', 'Report the sky brightness'),
        ),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 16),
            child: Center(child: Text('${_step.index + 1} / 3')),
          ),
        ],
      ),
      body: _body(context),
    ),
  );

  Widget _body(BuildContext context) {
    final error = _error;
    final chart = _chart;
    if (error != null) {
      return ListView(
        padding: const EdgeInsets.all(24),
        children: [Text(error, key: const Key('report-error'))],
      );
    }
    if (chart == null) return const Center(child: CircularProgressIndicator());
    return switch (_step) {
      _Step.prepare => _prepare(context, chart),
      _Step.compare => _compare(context, chart),
      _Step.sent => _sent(context),
    };
  }

  Widget _prepare(BuildContext context, ReportChart chart) {
    final theme = Theme.of(context);
    final waited = DateTime.now().difference(_started);
    final left = _adapt - waited;
    final wait = chart.nextAt?.toLocal();
    final checks = [
      (
        chart.sunAltitude <= -18,
        chart.sunAltitude <= -18
            ? tr(
                'Es ist dunkel genug (Sonne ${chart.sunAltitude.round()}°).',
                'It is dark enough (sun ${chart.sunAltitude.round()}°).',
              )
            : tr(
                'Es ist noch nicht dunkel genug.',
                'It is not dark enough yet.',
              ),
      ),
      (
        !chart.moonUp || chart.moonIllumination < 0.3,
        chart.moonUp
            ? tr(
                'Der Mond steht am Himmel (${(chart.moonIllumination * 100).round()} %). Melde lieber, wenn er untergegangen ist.',
                'The moon is up (${(chart.moonIllumination * 100).round()} %). Better report once it has set.',
              )
            : tr(
                'Der Mond ist unter dem Horizont.',
                'The moon is below the horizon.',
              ),
      ),
    ];
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        for (final (ok, text) in checks)
          ListTile(
            leading: Icon(ok ? Icons.check_circle : Icons.info_outline),
            title: Text(text),
          ),
        if (!chart.ready)
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(chart.reason ?? ''),
          )
        else ...[
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    tr(
                      '1. Augen an die Dunkelheit gewöhnen',
                      '1. Let your eyes adapt to the dark',
                    ),
                    style: theme.textTheme.titleMedium,
                  ),
                  const SizedBox(height: 6),
                  Text(
                    tr(
                      'Mindestens 15 Minuten ohne helles Licht. Dieser Bildschirm bleibt rot.',
                      'At least 15 minutes without bright light. This screen stays red.',
                    ),
                  ),
                  const SizedBox(height: 8),
                  LinearProgressIndicator(
                    value: (waited.inSeconds / _adapt.inSeconds).clamp(0, 1),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    left.isNegative
                        ? tr('Fertig.', 'Done.')
                        : tr(
                            'noch ${left.inMinutes + 1} Minuten',
                            '${left.inMinutes + 1} minutes to go',
                          ),
                    style: theme.textTheme.bodySmall,
                  ),
                ],
              ),
            ),
          ),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    tr(
                      '2. Das Sternbild finden: ${chart.name}',
                      '2. Find the constellation: ${chart.name}',
                    ),
                    style: theme.textTheme.titleMedium,
                  ),
                  const SizedBox(height: 6),
                  Text(
                    tr(
                      'Steht gerade im ${compassName(chart.azimuth ?? 0)}, ${(chart.altitude ?? 0).round()}° hoch.',
                      'Now in the ${compassName(chart.azimuth ?? 0)}, ${(chart.altitude ?? 0).round()}° up.',
                    ),
                  ),
                  const SizedBox(height: 8),
                  OutlinedButton(
                    key: const Key('report-guide'),
                    onPressed: () => Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) => SkyViewScreen(
                          client: widget.client,
                          load: () => widget.client.publicSkyView(
                            _place!.lat,
                            _place!.lon,
                          ),
                          targetKey: chart.key,
                          useCamera: false,
                        ),
                      ),
                    ),
                    child: Text(
                      tr('Mit dem Pfeil hinführen', 'Guide me there'),
                    ),
                  ),
                ],
              ),
            ),
          ),
          if (wait != null)
            Padding(
              padding: const EdgeInsets.all(8),
              child: Text(
                tr(
                  'Die nächste Meldung geht ab ${wait.hour.toString().padLeft(2, '0')}:${wait.minute.toString().padLeft(2, '0')} Uhr.',
                  'The next report is possible from ${wait.hour.toString().padLeft(2, '0')}:${wait.minute.toString().padLeft(2, '0')}.',
                ),
              ),
            ),
          const SizedBox(height: 12),
          FilledButton(
            key: const Key('report-found'),
            onPressed: wait != null
                ? null
                : () => setState(() => _step = _Step.compare),
            child: Text(
              tr(
                'Gefunden: weiter zu den Bildern',
                'Found it: on to the pictures',
              ),
            ),
          ),
          if (!left.isNegative)
            Text(
              tr(
                'Du kannst schon vergleichen, wenn du länger im Dunkeln bist.',
                'You can compare already if you have been in the dark for a while.',
              ),
              textAlign: TextAlign.center,
              style: theme.textTheme.bodySmall,
            ),
        ],
      ],
    );
  }

  Widget _compare(BuildContext context, ReportChart chart) {
    final theme = Theme.of(context);
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text(
          tr(
            'Welches Bild passt zu deinem Himmel? ${chart.name}, so wie es gerade steht.',
            'Which picture matches your sky? ${chart.name}, as it stands now.',
          ),
          style: theme.textTheme.titleMedium,
        ),
        const SizedBox(height: 10),
        GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 10,
          crossAxisSpacing: 10,
          children: [
            for (final limit in chart.limits)
              InkWell(
                key: Key('chart-$limit'),
                onTap: () => setState(() => _limit = limit),
                child: Container(
                  decoration: BoxDecoration(
                    color: Colors.black,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(
                      color: _limit == limit ? nightRed : Sky.line,
                      width: _limit == limit ? 3 : 1,
                    ),
                  ),
                  child: Column(
                    children: [
                      Expanded(
                        child: CustomPaint(
                          painter: StarChartPainter(chart.stars, limit),
                          size: Size.infinite,
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.only(bottom: 6),
                        child: Text(
                          tr('bis ${_mag(limit)} mag', 'to ${_mag(limit)} mag'),
                          style: theme.textTheme.bodySmall,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
          ],
        ),
        const SizedBox(height: 10),
        Text(
          tr(
            'Ehrlich bleiben: lieber das Bild mit weniger Sternen wählen.',
            'Be honest: rather pick the picture with fewer stars.',
          ),
          style: theme.textTheme.bodySmall,
        ),
        SwitchListTile(
          key: const Key('report-clouds'),
          title: Text(
            tr('Wolken oder Dunst stören', 'Clouds or haze are in the way'),
          ),
          value: _clouds,
          onChanged: (v) => setState(() => _clouds = v),
        ),
        FilledButton(
          key: const Key('report-send'),
          onPressed: _limit == null || _sending ? null : _send,
          child: Text(tr('Meldung abschicken', 'Send the report')),
        ),
      ],
    );
  }

  Widget _sent(BuildContext context) {
    final theme = Theme.of(context);
    final r = _result!;
    final sqm = (r['sqm'] as num).toDouble();
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text(
          tr('Danke für deine Meldung!', 'Thanks for your report!'),
          style: theme.textTheme.headlineSmall,
        ),
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  tr(
                    'Schwächste Sterne, die du siehst',
                    'Faintest stars you see',
                  ),
                  style: theme.textTheme.bodySmall,
                ),
                Text(
                  '${_mag((r['limit_mag'] as num).toDouble())} mag',
                  key: const Key('report-limit'),
                  style: theme.textTheme.headlineMedium,
                ),
                Text(
                  tr(
                    'etwa ${_mag(sqm, 1)} mag/″² · ${r['label']}',
                    'about ${_mag(sqm, 1)} mag/″² · ${r['label']}',
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 8),
        Text(
          tr(
            'Zum Vergleich: In der Stadt sieht man oft nur Sterne bis 3 mag, auf dem Land bis 6 mag. Die Milchstraße zeigt sich ab etwa 5,5 mag.',
            'For comparison: in a city you often see stars only to 3 mag, in the countryside to 6 mag. The Milky Way shows from about 5.5 mag.',
          ),
        ),
        const SizedBox(height: 12),
        OutlinedButton(
          onPressed: () => launchUrl(
            widget.client.site('/karte?layer=sqm'),
            mode: LaunchMode.externalApplication,
          ),
          child: Text(tr('Auf der Karte ansehen', 'See it on the map')),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(tr('Fertig', 'Done')),
        ),
      ],
    );
  }
}

String _mag(double v, [int digits = 1]) {
  final s = v.toStringAsFixed(digits);
  return appEnglish ? s : s.replaceAll('.', ',');
}

/// The stars down to [limit], bigger the brighter, on a black square.
class StarChartPainter extends CustomPainter {
  StarChartPainter(this.stars, this.limit);

  final List<({double x, double y, double mag})> stars;
  final double limit;

  /// Degrees across the chart (allskyhub_sky.report.SPAN_DEG).
  static const span = 28.0;

  @override
  void paint(Canvas canvas, Size size) {
    final scale = math.min(size.width, size.height) / span;
    final centre = size.center(Offset.zero);
    final paint = Paint()..color = Colors.white;
    for (final s in stars) {
      if (s.mag > limit) continue;
      final r = math.max(0.6, 3.2 - 0.45 * s.mag);
      canvas.drawCircle(centre + Offset(s.x * scale, -s.y * scale), r, paint);
    }
  }

  @override
  bool shouldRepaint(StarChartPainter old) =>
      old.stars != stars || old.limit != limit;
}
