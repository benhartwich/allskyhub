// Finding things in the public sky view: the search ("Was suchst du?"), the guide to a
// target, and the card that explains an object, with a freely licensed photo and its
// credit (hub /api/v1/public/sky-objects, photos from Wikimedia Commons via the hub).

import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/hub_client.dart';
import '../design.dart';
import '../l10n.dart';

const _bodies = {'planet', 'moon'};

String _where(SkyObject o) {
  final alt = o.dir.alt.round();
  if (alt < 0) return tr('unter dem Horizont', 'below the horizon');
  const de = [
    'Norden',
    'Nordosten',
    'Osten',
    'Südosten',
    'Süden',
    'Südwesten',
    'Westen',
    'Nordwesten',
  ];
  const en = [
    'north',
    'north-east',
    'east',
    'south-east',
    'south',
    'south-west',
    'west',
    'north-west',
  ];
  final dir = (appEnglish ? en : de)[(o.dir.az / 45).round() % 8];
  return tr('$dir, $alt° hoch', '$dir, $alt° up');
}

/// Worth a look tonight: up, and bright enough for the eye or binoculars.
bool worthIt(SkyObject o) =>
    o.dir.alt > 10 &&
    (_bodies.contains(o.kind) ||
        (o.kind != 'constellation' && (o.mag ?? 99) <= 6));

class SkyFindScreen extends StatefulWidget {
  const SkyFindScreen({super.key, required this.objects});

  final List<SkyObject> objects;

  @override
  State<SkyFindScreen> createState() => _SkyFindScreenState();
}

class _SkyFindScreenState extends State<SkyFindScreen> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final all = [
      for (final o in widget.objects)
        if (o.key != null) o,
    ];
    final q = _query.trim().toLowerCase();
    final found = q.isEmpty
        ? null
        : [
            for (final o in all)
              if (o.label.toLowerCase().contains(q) ||
                  o.key!.toLowerCase() == q)
                o,
          ];
    final tonight = [
      for (final o in all)
        if (worthIt(o)) o,
    ]..sort((a, b) => (a.mag ?? -30).compareTo(b.mag ?? -30));
    Widget tile(SkyObject o) => ListTile(
      leading: Icon(
        Icons.circle,
        size: 12,
        color: o.kind == 'constellation'
            ? Sky.accent
            : _bodies.contains(o.kind)
            ? Sky.warn
            : Sky.accent2,
      ),
      title: Text(o.label),
      subtitle: Text(_where(o)),
      trailing: o.dir.alt > 0
          ? Text(
              tr('sichtbar', 'visible'),
              style: theme.textTheme.labelSmall?.copyWith(color: Sky.aurora),
            )
          : null,
      onTap: () => Navigator.pop(context, o),
    );
    return Scaffold(
      appBar: AppBar(
        title: TextField(
          key: const Key('find-query'),
          autofocus: true,
          onChanged: (v) => setState(() => _query = v),
          decoration: InputDecoration(
            hintText: tr(
              'Planet, Sternbild, Galaxie …',
              'Planet, constellation, galaxy …',
            ),
            border: InputBorder.none,
          ),
        ),
      ),
      body: ListView(
        children: [
          if (found == null) ...[
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
              child: Text(
                tr('HEUTE NACHT LOHNT SICH', 'WORTH A LOOK TONIGHT'),
                style: theme.textTheme.labelSmall,
              ),
            ),
            for (final o in tonight) tile(o),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                tr(
                  'Alle Planeten, die 88 Sternbilder und die 110 Messier-Objekte sind durchsuchbar.',
                  'All planets, the 88 constellations and the 110 Messier objects can be searched.',
                ),
                style: theme.textTheme.bodySmall,
              ),
            ),
          ] else if (found.isEmpty)
            Padding(
              padding: const EdgeInsets.all(24),
              child: Text(tr('Nichts gefunden.', 'Nothing found.')),
            )
          else
            for (final o in found) tile(o),
        ],
      ),
    );
  }
}

/// Where to turn the phone toward the target, and "found" once it is near the middle.
class TargetCard extends StatelessWidget {
  const TargetCard({
    super.key,
    required this.target,
    required this.looking,
    required this.onInfo,
    required this.onEnd,
  });

  final SkyObject target;
  final SkyDir looking;
  final VoidCallback onInfo;
  final VoidCallback onEnd;

  /// Angle between where the phone looks and the target, degrees.
  static double offBy(SkyDir a, SkyDir b) {
    double r(double d) => d * math.pi / 180;
    final c =
        math.sin(r(a.alt)) * math.sin(r(b.alt)) +
        math.cos(r(a.alt)) * math.cos(r(b.alt)) * math.cos(r(a.az - b.az));
    return math.acos(c.clamp(-1.0, 1.0)) * 180 / math.pi;
  }

  static String guide(SkyDir looking, SkyDir target) {
    if (offBy(looking, target) < 6) return tr('Gefunden!', 'Found it!');
    final up = target.alt - looking.alt;
    final right = ((target.az - looking.az + 540) % 360) - 180;
    final parts = [
      if (up.abs() >= 5)
        up > 0
            ? tr('${up.round()}° höher', '${up.round()}° higher')
            : tr('${(-up).round()}° tiefer', '${(-up).round()}° lower'),
      if (right.abs() >= 5)
        right > 0
            ? tr(
                '${right.round()}° nach rechts',
                '${right.round()}° to the right',
              )
            : tr(
                '${(-right).round()}° nach links',
                '${(-right).round()}° to the left',
              ),
    ];
    final text = parts.join(tr(' und ', ' and '));
    return target.alt < 0
        ? tr('$text (unter dem Horizont)', '$text (below the horizon)')
        : text;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      key: const Key('sky-target'),
      color: Sky.ink.withValues(alpha: .85),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: Sky.warn.withValues(alpha: .6)),
      ),
      child: ListTile(
        onTap: onInfo,
        title: Text(tr('Ziel: ${target.label}', 'Target: ${target.label}')),
        subtitle: Text(guide(looking, target.dir)),
        trailing: TextButton(
          onPressed: onEnd,
          child: Text(tr('Ende', 'Done'), style: theme.textTheme.labelLarge),
        ),
      ),
    );
  }
}

/// What an object is, with a photo and its credit; "Hinführen" returns true.
class SkyObjectSheet extends StatefulWidget {
  const SkyObjectSheet({super.key, required this.client, required this.object});

  final HubClient client;
  final SkyObject object;

  @override
  State<SkyObjectSheet> createState() => _SkyObjectSheetState();
}

class _SkyObjectSheetState extends State<SkyObjectSheet> {
  late final Future<SkyObjectInfo> _info = widget.client.skyObject(
    widget.object.key!,
  );

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final o = widget.object;
    return SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxHeight: MediaQuery.sizeOf(context).height * .85,
        ),
        child: FutureBuilder<SkyObjectInfo>(
          future: _info,
          builder: (context, snapshot) {
            final info = snapshot.data;
            final photo = info?.photo;
            return ListView(
              shrinkWrap: true,
              padding: const EdgeInsets.fromLTRB(18, 8, 18, 18),
              children: [
                if (photo != null) ...[
                  ClipRRect(
                    borderRadius: BorderRadius.circular(12),
                    child: AspectRatio(
                      aspectRatio: 4 / 3,
                      child: ColoredBox(
                        color: Colors.black,
                        child: Image.network(
                          widget.client.site(photo['url'] as String).toString(),
                          fit: BoxFit.contain,
                          errorBuilder: (_, _, _) => const SizedBox.shrink(),
                        ),
                      ),
                    ),
                  ),
                  InkWell(
                    key: const Key('photo-credit'),
                    onTap: () => launchUrl(
                      Uri.parse(photo['page'] as String),
                      mode: LaunchMode.externalApplication,
                    ),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 6),
                      child: Text(
                        tr(
                          'Foto: ${photo['author']} · ${photo['licence']} · Wikimedia Commons',
                          'Photo: ${photo['author']} · ${photo['licence']} · Wikimedia Commons',
                        ),
                        style: theme.textTheme.bodySmall,
                      ),
                    ),
                  ),
                ],
                Text(
                  (info?.kindLabel ?? '').toUpperCase(),
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: Sky.accent2,
                  ),
                ),
                Text(o.label, style: theme.textTheme.headlineSmall),
                const SizedBox(height: 8),
                if (snapshot.hasError)
                  Text(
                    tr(
                      'Keine Beschreibung verfügbar.',
                      'No description available.',
                    ),
                  )
                else if (info == null)
                  const LinearProgressIndicator()
                else
                  Text(info.text),
                const SizedBox(height: 10),
                Text(
                  [
                    _where(o),
                    if (o.mag != null)
                      tr(
                        'Helligkeit ${o.mag!.toStringAsFixed(1).replaceAll('.', ',')} mag',
                        'brightness ${o.mag!.toStringAsFixed(1)} mag',
                      ),
                  ].join(' · '),
                  style: theme.textTheme.bodySmall,
                ),
                const SizedBox(height: 14),
                FilledButton(
                  key: const Key('guide-me'),
                  onPressed: () => Navigator.pop(context, true),
                  child: Text(tr('Hinführen', 'Guide me there')),
                ),
              ],
            );
          },
        ),
      ),
    );
  }
}
