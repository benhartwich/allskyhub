// Stage 5: reporting what one saw (a fireball, aurora with an optional photo), the aurora
// reports nearby, and the alerts at a chosen place (hub api/app_sightings.py). Reporting
// needs an account, like the sky brightness; photos show only after the hub's admins
// approved them, under CC BY 4.0 with the name the reporter gives.

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/hub_client.dart';
import '../design.dart';
import '../l10n.dart';
import '../platform/location.dart';
import '../platform/push.dart';
import 'camera_status.dart';
import 'sky_view_screen.dart';

const _brightnessOptions = {
  'star': ('wie ein Stern', 'like a star'),
  'venus': ('wie Venus', 'like Venus'),
  'moon': ('wie der Mond', 'like the moon'),
  'day': ('taghell', 'bright as day'),
};
const _fireballDetails = {
  'green': ('grün', 'green'),
  'orange': ('orange', 'orange'),
  'red': ('rot', 'red'),
  'blue': ('blau', 'blue'),
  'broke': ('zerbrach', 'broke up'),
  'trail': ('Schweif blieb', 'trail stayed'),
  'sound': ('Geräusch', 'sound'),
};
const _auroraDetails = {
  'glow': ('Leuchten am Horizont', 'glow on the horizon'),
  'rays': ('Säulen / Strahlen', 'pillars / rays'),
  'green': ('grün', 'green'),
  'red': ('rot', 'red'),
  'purple': ('violett', 'purple'),
};

String _t((String, String) pair) => tr(pair.$1, pair.$2);

Future<({double lat, double lon})?> _here(
  BuildContext context,
  LocationSource location,
) async {
  final found = await location.current();
  final at = found.location;
  if (at == null && context.mounted) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          tr(
            'Ohne deinen ungefähren Standort geht das Melden nicht.',
            'Reporting needs your rough location.',
          ),
        ),
      ),
    );
  }
  return at == null ? null : (lat: at.latitude, lon: at.longitude);
}

class _Chips extends StatelessWidget {
  const _Chips({
    required this.options,
    required this.selected,
    required this.onTap,
    this.prefix = '',
  });

  final Map<String, (String, String)> options;
  final Set<String> selected;
  final ValueChanged<String> onTap;
  final String prefix;

  @override
  Widget build(BuildContext context) => Wrap(
    spacing: 8,
    runSpacing: 6,
    children: [
      for (final MapEntry(key: key, value: label) in options.entries)
        FilterChip(
          key: Key('$prefix$key'),
          label: Text(_t(label)),
          selected: selected.contains(key),
          onSelected: (_) => onTap(key),
        ),
    ],
  );
}

/// Hold the phone where something was and tap: altitude and azimuth.
class PointScreen extends StatefulWidget {
  const PointScreen({super.key, required this.title, this.poseSource});

  final String title;
  final PoseSource? poseSource;

  @override
  State<PointScreen> createState() => _PointScreenState();
}

class _PointScreenState extends State<PointScreen> {
  StreamSubscription<Pose>? _sub;
  SkyDir? _looking;

  @override
  void initState() {
    super.initState();
    _sub = (widget.poseSource ?? SensorPoseSource()).poses.listen((pose) {
      if (mounted) setState(() => _looking = pose.looking);
    }, onError: (Object _) {});
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final looking = _looking;
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.my_location, size: 64, color: Sky.accent),
              const SizedBox(height: 16),
              Text(
                tr(
                  'Halte die Rückseite des Handys genau dorthin und tippe auf „Hier“.',
                  'Point the back of the phone exactly there and tap “Here”.',
                ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 12),
              Text(
                looking == null
                    ? tr(
                        'Warte auf die Sensoren …',
                        'Waiting for the sensors …',
                      )
                    : tr(
                        '${compassName(looking.az)}, ${looking.alt.round()}° hoch',
                        '${compassName(looking.az)}, ${looking.alt.round()}° up',
                      ),
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 20),
              FilledButton(
                key: const Key('point-here'),
                onPressed: looking == null
                    ? null
                    : () => Navigator.pop(context, looking),
                child: Text(tr('Hier', 'Here')),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class FireballScreen extends StatefulWidget {
  const FireballScreen({
    super.key,
    required this.client,
    required this.location,
    this.poseSource,
  });

  final HubClient client;
  final LocationSource location;
  final PoseSource? poseSource;

  @override
  State<FireballScreen> createState() => _FireballScreenState();
}

class _FireballScreenState extends State<FireballScreen> {
  int _minutesAgo = 0;
  TimeOfDay? _time;
  SkyDir? _start;
  SkyDir? _end;
  String? _brightness;
  final _details = <String>{};
  bool _sending = false;
  Map<String, dynamic>? _result;

  DateTime get _seenAt {
    final now = DateTime.now();
    final t = _time;
    if (t == null) return now.subtract(Duration(minutes: _minutesAgo));
    var at = DateTime(now.year, now.month, now.day, t.hour, t.minute);
    if (at.isAfter(now)) at = at.subtract(const Duration(days: 1));
    return at;
  }

  Future<void> _point(bool start) async {
    final dir = await Navigator.of(context).push<SkyDir>(
      MaterialPageRoute(
        builder: (_) => PointScreen(
          title: start
              ? tr('Wo tauchte sie auf?', 'Where did it appear?')
              : tr('Wo verlosch sie?', 'Where did it end?'),
          poseSource: widget.poseSource,
        ),
      ),
    );
    if (dir != null && mounted) {
      setState(() => start ? _start = dir : _end = dir);
    }
  }

  Future<void> _send() async {
    final here = await _here(context, widget.location);
    if (here == null) return;
    setState(() => _sending = true);
    try {
      final result = await widget.client.reportFireball(
        seenAt: _seenAt,
        lat: here.lat,
        lon: here.lon,
        start: _start,
        end: _end,
        brightness: _brightness!,
        details: [..._details],
      );
      if (mounted) setState(() => _result = result);
    } on HubException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.message)));
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  String _dir(SkyDir? d) => d == null
      ? tr('zeigen und tippen', 'point and tap')
      : '${compassName(d.az)}, ${d.alt.round()}°';

  @override
  Widget build(BuildContext context) {
    final result = _result;
    return Scaffold(
      appBar: AppBar(
        title: Text(tr('Feuerkugel gesehen?', 'Seen a fireball?')),
      ),
      body: result != null ? _matches(context, result) : _form(context),
    );
  }

  Widget _form(BuildContext context) {
    final theme = Theme.of(context);
    Widget section(String title, Widget child) => Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: theme.textTheme.titleMedium),
            const SizedBox(height: 8),
            child,
          ],
        ),
      ),
    );
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        section(
          tr('Wann?', 'When?'),
          Wrap(
            spacing: 8,
            children: [
              for (final (minutes, label) in [
                (0, tr('gerade eben', 'just now')),
                (10, tr('vor 10 Min.', '10 min ago')),
              ])
                ChoiceChip(
                  label: Text(label),
                  selected: _time == null && _minutesAgo == minutes,
                  onSelected: (_) => setState(() {
                    _time = null;
                    _minutesAgo = minutes;
                  }),
                ),
              ChoiceChip(
                label: Text(
                  _time == null
                      ? tr('Uhrzeit wählen', 'Pick a time')
                      : _time!.format(context),
                ),
                selected: _time != null,
                onSelected: (_) async {
                  final t = await showTimePicker(
                    context: context,
                    initialTime: TimeOfDay.now(),
                  );
                  if (t != null) setState(() => _time = t);
                },
              ),
            ],
          ),
        ),
        section(
          tr('Wo am Himmel?', 'Where in the sky?'),
          Row(
            children: [
              for (final (start, label, dir) in [
                (true, tr('Anfang', 'Start'), _start),
                (false, tr('Ende', 'End'), _end),
              ])
                Expanded(
                  child: Padding(
                    padding: EdgeInsets.only(right: start ? 8 : 0),
                    child: OutlinedButton(
                      key: Key(start ? 'fireball-start' : 'fireball-end'),
                      onPressed: () => _point(start),
                      child: Column(
                        children: [
                          Text(label, style: theme.textTheme.labelSmall),
                          Text(_dir(dir)),
                        ],
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
        section(
          tr('Wie hell?', 'How bright?'),
          _Chips(
            prefix: 'bright-',
            options: _brightnessOptions,
            selected: {?_brightness},
            onTap: (k) => setState(() => _brightness = k),
          ),
        ),
        section(
          tr('Noch etwas? (freiwillig)', 'Anything else? (optional)'),
          _Chips(
            options: _fireballDetails,
            selected: _details,
            onTap: (k) => setState(
              () => _details.contains(k) ? _details.remove(k) : _details.add(k),
            ),
          ),
        ),
        FilledButton(
          key: const Key('fireball-send'),
          onPressed: _brightness == null || _sending ? null : _send,
          child: Text(tr('Melden', 'Report')),
        ),
        const SizedBox(height: 6),
        Text(
          tr(
            'Ort auf etwa 10 km gerundet · ohne Namen',
            'Place rounded to about 10 km · without your name',
          ),
          textAlign: TextAlign.center,
          style: theme.textTheme.bodySmall,
        ),
      ],
    );
  }

  Widget _matches(BuildContext context, Map<String, dynamic> result) {
    final theme = Theme.of(context);
    final cameras = result['cameras'] as List<dynamic>;
    final tracks = result['tracks'] as List<dynamic>;
    final others = result['others_km'] as List<dynamic>;
    final named = [
      for (final c in cameras)
        if ((c as Map<String, dynamic>)['title'] != null) c,
    ];
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text(
          tr('Danke für deine Meldung!', 'Thanks for your report!'),
          style: theme.textTheme.headlineSmall,
        ),
        const SizedBox(height: 12),
        if (cameras.isNotEmpty)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    tr('AUCH VON KAMERAS GESEHEN', 'ALSO SEEN BY CAMERAS'),
                    style: theme.textTheme.labelSmall?.copyWith(
                      color: Sky.aurora,
                    ),
                  ),
                  Text(
                    tr(
                      '${cameras.length} Kamera${cameras.length == 1 ? '' : 's'} um diese Zeit',
                      '${cameras.length} camera${cameras.length == 1 ? '' : 's'} around that time',
                    ),
                    style: theme.textTheme.titleMedium,
                  ),
                  const SizedBox(height: 8),
                  for (final c in named)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          if (c['image'] case final String image)
                            ClipRRect(
                              borderRadius: BorderRadius.circular(10),
                              child: Image.network(
                                widget.client.site(image).toString(),
                                height: 160,
                                fit: BoxFit.cover,
                                errorBuilder: (_, _, _) =>
                                    const SizedBox.shrink(),
                              ),
                            ),
                          Text(
                            '${c['title']}',
                            style: theme.textTheme.bodySmall,
                          ),
                        ],
                      ),
                    ),
                ],
              ),
            ),
          )
        else
          Text(
            tr(
              'Noch keine Kamera hat zu dieser Zeit etwas gemeldet. Kameras laden ihre Erkennungen manchmal später hoch; deine Meldung bleibt gespeichert.',
              'No camera has reported anything at that time yet. Cameras sometimes upload their detections later; your report is kept.',
            ),
          ),
        for (final t in tracks)
          ListTile(
            leading: const Icon(Icons.timeline),
            title: Text(tr('Bahn vermessen: ansehen', 'Track measured: view')),
            onTap: () => launchUrl(
              widget.client.site(t as String),
              mode: LaunchMode.externalApplication,
            ),
          ),
        if (others.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              tr(
                'Andere Meldungen zu dieser Zeit: ${others.length} (${others.map((d) => '${(d as num).round()} km').join(', ')} entfernt)',
                'Other reports at that time: ${others.length} (${others.map((d) => '${(d as num).round()} km').join(', ')} away)',
              ),
            ),
          ),
        const SizedBox(height: 16),
        FilledButton(
          onPressed: () => Navigator.pop(context),
          child: Text(tr('Fertig', 'Done')),
        ),
      ],
    );
  }
}

class AuroraScreen extends StatefulWidget {
  const AuroraScreen({
    super.key,
    required this.client,
    required this.location,
    this.pickPhoto,
  });

  final HubClient client;
  final LocationSource location;

  /// The system's photo picker; a fake in tests.
  final Future<(Uint8List, String)?> Function()? pickPhoto;

  @override
  State<AuroraScreen> createState() => _AuroraScreenState();
}

class _AuroraScreenState extends State<AuroraScreen> {
  bool? _nakedEye;
  final _details = <String>{};
  (Uint8List, String)? _photo;
  final _author = TextEditingController();
  bool _licence = false;
  bool _sending = false;

  @override
  void dispose() {
    _author.dispose();
    super.dispose();
  }

  Future<(Uint8List, String)?> _pick() async {
    // Resized and re-compressed by the picker: always JPEG (also an iPhone's HEIC).
    final file = await ImagePicker().pickImage(
      source: ImageSource.gallery,
      maxWidth: 3000,
      maxHeight: 3000,
      imageQuality: 90,
    );
    return file == null ? null : (await file.readAsBytes(), file.name);
  }

  Future<void> _send() async {
    final here = await _here(context, widget.location);
    if (here == null || !mounted) return;
    setState(() => _sending = true);
    try {
      final photo = _photo;
      final mine = await widget.client.reportAurora(
        lat: here.lat,
        lon: here.lon,
        nakedEye: _nakedEye!,
        details: [..._details],
        photo: photo?.$1,
        photoName: photo?.$2 ?? 'photo.jpg',
        author: _author.text,
      );
      if (!mounted) return;
      await Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => AuroraListScreen(
            client: widget.client,
            lat: here.lat,
            lon: here.lon,
            mine: mine,
          ),
        ),
      );
    } on HubException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.message)));
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final photo = _photo;
    return Scaffold(
      appBar: AppBar(
        title: Text(tr('Polarlicht gesehen?', 'Seen the aurora?')),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(
            tr('Wie hast du es gesehen?', 'How did you see it?'),
            style: theme.textTheme.titleMedium,
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            children: [
              for (final (eye, label) in [
                (true, tr('mit bloßem Auge', 'with the naked eye')),
                (false, tr('nur auf dem Foto', 'only on a photo')),
              ])
                ChoiceChip(
                  key: Key('aurora-eye-$eye'),
                  label: Text(label),
                  selected: _nakedEye == eye,
                  onSelected: (_) => setState(() => _nakedEye = eye),
                ),
            ],
          ),
          const SizedBox(height: 16),
          Text(tr('Was?', 'What?'), style: theme.textTheme.titleMedium),
          const SizedBox(height: 8),
          _Chips(
            options: _auroraDetails,
            selected: _details,
            onTap: (k) => setState(
              () => _details.contains(k) ? _details.remove(k) : _details.add(k),
            ),
          ),
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      if (photo != null)
                        ClipRRect(
                          borderRadius: BorderRadius.circular(8),
                          child: Image.memory(
                            photo.$1,
                            width: 72,
                            height: 72,
                            fit: BoxFit.cover,
                          ),
                        ),
                      Expanded(
                        child: TextButton.icon(
                          key: const Key('aurora-photo'),
                          icon: const Icon(Icons.add_photo_alternate_outlined),
                          label: Text(
                            photo == null
                                ? tr(
                                    'Foto hinzufügen (freiwillig)',
                                    'Add a photo (optional)',
                                  )
                                : tr('Anderes Foto', 'Other photo'),
                          ),
                          onPressed: () async {
                            final picked = await (widget.pickPhoto ?? _pick)();
                            if (picked != null && mounted) {
                              setState(() => _photo = picked);
                            }
                          },
                        ),
                      ),
                    ],
                  ),
                  if (photo != null) ...[
                    TextField(
                      key: const Key('aurora-author'),
                      controller: _author,
                      maxLength: 100,
                      decoration: InputDecoration(
                        labelText: tr(
                          'Name unter dem Foto (leer: anonym)',
                          'Name under the photo (empty: anonymous)',
                        ),
                      ),
                    ),
                    CheckboxListTile(
                      key: const Key('aurora-licence'),
                      contentPadding: EdgeInsets.zero,
                      value: _licence,
                      onChanged: (v) => setState(() => _licence = v ?? false),
                      title: Text(
                        tr(
                          'Ich habe das Foto selbst gemacht und gebe es unter CC BY 4.0 frei. Es erscheint erst nach Prüfung, ohne Ort und Kameradaten.',
                          'I took this photo myself and release it under CC BY 4.0. It shows only after a check, without place or camera data.',
                        ),
                        style: theme.textTheme.bodySmall,
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          FilledButton(
            key: const Key('aurora-send'),
            style: FilledButton.styleFrom(backgroundColor: Sky.aurora),
            onPressed:
                _nakedEye == null || _sending || (photo != null && !_licence)
                ? null
                : _send,
            child: Text(
              tr('Melden und andere warnen', 'Report and tell others'),
            ),
          ),
          const SizedBox(height: 6),
          Text(
            tr(
              'Wer in der Nähe Polarlicht-Nachrichten eingeschaltet hat, erfährt es. Ort auf etwa 10 km gerundet.',
              'People nearby with aurora alerts switched on will hear of it. Place rounded to about 10 km.',
            ),
            textAlign: TextAlign.center,
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

/// Aurora reports of the last hours, nearest first, with approved photos.
class AuroraListScreen extends StatefulWidget {
  const AuroraListScreen({
    super.key,
    required this.client,
    required this.lat,
    required this.lon,
    this.mine,
  });

  final HubClient client;
  final double lat;
  final double lon;

  /// Just sent: thanks, and the photo's state.
  final AuroraSighting? mine;

  @override
  State<AuroraListScreen> createState() => _AuroraListScreenState();
}

class _AuroraListScreenState extends State<AuroraListScreen> {
  late Future<List<AuroraSighting>> _list = widget.client.auroraNearby(
    widget.lat,
    widget.lon,
  );
  final _flagged = <String>{};

  Future<void> _flag(AuroraSighting s) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(tr('Foto melden?', 'Report this photo?')),
        content: Text(
          tr(
            'Zeigt das Foto etwas Unpassendes oder kein Polarlicht? Nach mehreren Meldungen prüfen wir es noch einmal.',
            'Does the photo show something unsuitable or no aurora? After several reports we check it again.',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(tr('Abbrechen', 'Cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(tr('Melden', 'Report')),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.client.flagAurora(s.id);
      if (mounted) setState(() => _flagged.add(s.id));
    } on HubException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final mine = widget.mine;
    return Scaffold(
      appBar: AppBar(title: Text(tr('Polarlicht-Meldungen', 'Aurora reports'))),
      body: RefreshIndicator(
        onRefresh: () async {
          final next = widget.client.auroraNearby(widget.lat, widget.lon);
          setState(() => _list = next);
          await next;
        },
        child: FutureBuilder<List<AuroraSighting>>(
          future: _list,
          builder: (context, snapshot) {
            final list = snapshot.data;
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                if (mine != null)
                  Card(
                    color: Sky.aurora.withValues(alpha: .15),
                    child: ListTile(
                      title: Text(
                        tr(
                          'Danke für deine Meldung!',
                          'Thanks for your report!',
                        ),
                      ),
                      subtitle: Text(
                        mine.photoState == 'pending'
                            ? tr(
                                'Dein Foto erscheint, sobald wir es geprüft haben.',
                                'Your photo shows once we have checked it.',
                              )
                            : tr(
                                'Andere in der Nähe erfahren davon.',
                                'Others nearby will hear of it.',
                              ),
                      ),
                    ),
                  ),
                if (list == null && !snapshot.hasError)
                  const Center(child: CircularProgressIndicator()),
                if (list != null && list.isEmpty)
                  Text(
                    tr(
                      'Keine Meldungen in den letzten Stunden.',
                      'No reports in the last hours.',
                    ),
                  ),
                for (final s in list ?? <AuroraSighting>[])
                  Card(
                    clipBehavior: Clip.antiAlias,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        if (s.thumb case final String thumb)
                          Image.network(
                            widget.client.site(thumb).toString(),
                            height: 180,
                            fit: BoxFit.cover,
                            errorBuilder: (_, _, _) => const SizedBox.shrink(),
                          ),
                        ListTile(
                          title: Text(
                            '${s.nakedEye ? tr('Mit bloßem Auge', 'Naked eye') : tr('Auf dem Foto', 'On a photo')} · ${s.distanceKm.round()} km',
                          ),
                          subtitle: Text(
                            [
                              formatAge(s.at),
                              ...s.details.map(
                                (d) => _auroraDetails[d] == null
                                    ? d
                                    : _t(_auroraDetails[d]!),
                              ),
                              if (s.author != null)
                                tr(
                                  'Foto: ${s.author} · CC BY 4.0',
                                  'Photo: ${s.author} · CC BY 4.0',
                                ),
                            ].join(' · '),
                          ),
                          trailing:
                              s.photo == null || widget.client.token == null
                              ? null
                              : IconButton(
                                  key: Key('flag-${s.id}'),
                                  tooltip: tr('Foto melden', 'Report photo'),
                                  icon: Icon(
                                    _flagged.contains(s.id)
                                        ? Icons.flag
                                        : Icons.outlined_flag,
                                  ),
                                  onPressed: _flagged.contains(s.id)
                                      ? null
                                      : () => _flag(s),
                                ),
                        ),
                      ],
                    ),
                  ),
                Text(
                  tr(
                    'Polarlicht zeigt sich oft zuerst nur auf Fotos. Schau Richtung Pol: bei uns nach Norden.',
                    'Aurora often shows on photos first. Look toward the pole: north for us.',
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
}

/// The alerts at a place: aurora seen nearby, fireballs measured nearby, a clear night.
class AlertsScreen extends StatefulWidget {
  const AlertsScreen({super.key, required this.client, required this.location});

  final HubClient client;
  final LocationSource location;

  @override
  State<AlertsScreen> createState() => _AlertsScreenState();
}

class _AlertsScreenState extends State<AlertsScreen> {
  Map<String, dynamic>? _alerts;
  bool? _push;
  String? _error;

  @override
  void initState() {
    super.initState();
    widget.client.alerts().then(
      (a) => mounted ? setState(() => _alerts = a) : null,
      onError: (Object e) => mounted
          ? setState(() => _error = tr('Nicht erreichbar.', 'Not reachable.'))
          : null,
    );
    widget.client.accountSettings().then(
      (s) => mounted ? setState(() => _push = s.pushAvailable) : null,
      onError: (Object _) {},
    );
  }

  Future<void> _set(Map<String, Object?> change) async {
    final turningOn = change.values.contains(true);
    if (turningOn && _alerts?['lat'] == null) {
      final here = await _here(context, widget.location);
      if (here == null) return;
      change = {
        ...change,
        'lat': here.lat,
        'lon': here.lon,
        'tz': ?await widget.location.timezone(),
      };
    }
    try {
      final next = await widget.client.updateAlerts(change);
      if (mounted) setState(() => _alerts = next);
      if (turningOn) unawaited(pushService.register(widget.client));
    } on HubException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final a = _alerts;
    return Scaffold(
      appBar: AppBar(title: Text(tr('Benachrichtigungen', 'Notifications'))),
      body: a == null
          ? Center(
              child: _error == null
                  ? const CircularProgressIndicator()
                  : Text(_error!),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                if (_push == false)
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.info_outline),
                      title: Text(
                        tr(
                          'Push-Nachrichten sind auf diesem Hub noch nicht eingerichtet. Deine Wahl gilt, sobald sie kommen.',
                          'Push messages are not set up on this hub yet. Your choice applies once they come.',
                        ),
                      ),
                    ),
                  ),
                for (final (key, title, sub) in [
                  (
                    'aurora',
                    tr('Polarlicht in der Nähe', 'Aurora nearby'),
                    tr('Meldungen anderer', 'Reports by others'),
                  ),
                  (
                    'fireball',
                    tr('Feuerkugel in der Nähe', 'Fireball nearby'),
                    tr('von Kameras vermessen', 'measured by cameras'),
                  ),
                  (
                    'clear_night',
                    tr('Klare Nacht bei dir', 'Clear night where you are'),
                    tr(
                      'am Abend, wenn es klar wird',
                      'in the evening, when it clears',
                    ),
                  ),
                ])
                  SwitchListTile(
                    key: Key('alert-$key'),
                    title: Text(title),
                    subtitle: Text(sub),
                    value: a[key] as bool? ?? false,
                    onChanged: (v) => _set({key: v}),
                  ),
                const SizedBox(height: 8),
                Text(
                  tr('Umkreis', 'Radius'),
                  style: theme.textTheme.titleSmall,
                ),
                Wrap(
                  spacing: 8,
                  children: [
                    for (final km in const [100, 300, 500])
                      ChoiceChip(
                        key: Key('radius-$km'),
                        label: Text('$km km'),
                        selected: a['radius_km'] == km,
                        onSelected: (_) => _set({'radius_km': km}),
                      ),
                  ],
                ),
                const SizedBox(height: 12),
                Text(
                  tr(
                    'Dafür merkt sich der Hub deinen Ort auf etwa 10 km gerundet. Schaltest du alle drei aus, löscht er ihn.',
                    'For this the hub keeps your place, rounded to about 10 km. Switching all three off deletes it.',
                  ),
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
    );
  }
}
